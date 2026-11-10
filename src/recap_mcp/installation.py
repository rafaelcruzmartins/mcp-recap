"""Descoberta da instalação do Autodesk ReCap e diagnóstico das capacidades do DeCap.

Ordem de busca do diretório de instalação:
1. Variável de ambiente ``RECAP_HOME`` (pasta que contém ``decap.exe``).
2. Registro do Windows (chaves de desinstalação com "ReCap" no nome).
3. Subpastas de ``%ProgramFiles%\\Autodesk`` cujo nome contém "recap".

A presença de ``decap.exe`` não prova licença válida. O estado da licença só é
consultado quando solicitado explicitamente (``decap.exe --checkLicense``) e, sem
essa consulta, é informado como ``desconhecido``.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors
from .decap_messages import parse_messages

IS_WINDOWS = sys.platform == "win32"
HELP_TIMEOUT_S = 60
LICENSE_TIMEOUT_S = 120

# Opções de importação que este servidor sabe montar. Cada uma só é liberada
# quando aparece no texto de ``decap.exe --help`` da versão instalada.
KNOWN_IMPORT_OPTIONS = (
    "--minRangeClipping",
    "--maxRangeClipping",
    "--decimation",
    "--unify",
    "--normalizeIntensity",
    "--currentCoordinateSystem",
    "--targetCoordinateSystem",
    "--inputUnitType",
    "--e57EnableImageImport",
    "--e57CreateScanPerImage",
    "--e57MatchImageResolution",
    "--e57LevelImage",
)
KNOWN_MODES = ("--importWithLicense", "--import", "--checkLicense", "--version", "--controlFile")


@dataclass
class Installation:
    found: bool
    install_dir: str | None = None
    decap_exe: str | None = None
    recap_exe: str | None = None
    product_version: str | None = None
    decap_file_version: str | None = None
    source: str | None = None
    searched: list[str] = field(default_factory=list)
    registry_products: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "install_dir": self.install_dir,
            "decap_exe": self.decap_exe,
            "recap_exe": self.recap_exe,
            "product_version": self.product_version,
            "decap_file_version": self.decap_file_version,
            "detected_by": self.source,
            "searched": self.searched,
            "registry_products": self.registry_products,
        }


# ---------------------------------------------------------------------------
# Versão de arquivo via API do Windows (não depende de PowerShell no PATH)
# ---------------------------------------------------------------------------

class _FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "signature", "struc_version", "file_ms", "file_ls", "product_ms", "product_ls",
        "flags_mask", "flags", "os", "type", "subtype", "date_ms", "date_ls")]


def file_version(path: str | Path) -> str | None:
    """Lê a FileVersion de um executável Windows; ``None`` se indisponível."""
    if not IS_WINDOWS:
        return None
    try:
        api = ctypes.WinDLL("version.dll")
    except OSError:
        return None
    p = ctypes.c_wchar_p(str(path))
    api.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    size = api.GetFileVersionInfoSizeW(p, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(p, 0, size, buf):
        return None
    ptr, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not api.VerQueryValueW(buf, ctypes.c_wchar_p("\\"), ctypes.byref(ptr), ctypes.byref(length)):
        return None
    if not ptr or length.value < ctypes.sizeof(_FixedFileInfo):
        return None
    info = ctypes.cast(ptr, ctypes.POINTER(_FixedFileInfo)).contents
    if info.signature != 0xFEEF04BD:
        return None
    return f"{info.file_ms >> 16}.{info.file_ms & 0xFFFF}.{info.file_ls >> 16}.{info.file_ls & 0xFFFF}"


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------

def _reg_value(winreg: Any, key: Any, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _registry_products() -> list[dict[str, str]]:
    if not IS_WINDOWS:
        return []
    import winreg

    found: list[dict[str, str]] = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, key_path in roots:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                except OSError:
                    continue
                with sub:
                    name = _reg_value(winreg, sub, "DisplayName")
                    if "recap" in name.lower():
                        found.append({
                            "name": name,
                            "version": _reg_value(winreg, sub, "DisplayVersion"),
                            "install_location": _reg_value(winreg, sub, "InstallLocation"),
                        })
    return found


def _candidate_dirs(registry: list[dict[str, str]]) -> list[tuple[Path, str]]:
    cands: list[tuple[Path, str]] = []
    env = os.environ.get("RECAP_HOME")
    if env:
        cands.append((Path(env), "RECAP_HOME"))
    for prod in registry:
        loc = prod.get("install_location")
        if loc:
            base = Path(loc)
            cands.append((base, "registro"))
            if base.is_dir():
                cands.extend((c, "registro") for c in sorted(base.iterdir())
                             if c.is_dir() and "recap" in c.name.lower())
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(var)
        if not root:
            continue
        adsk = Path(root) / "Autodesk"
        if adsk.is_dir():
            try:
                cands.extend((c, "Program Files") for c in sorted(adsk.iterdir())
                             if c.is_dir() and "recap" in c.name.lower())
            except OSError:
                pass
    # remove duplicados mantendo a ordem
    seen: set[str] = set()
    unique = []
    for path, src in cands:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append((path, src))
    return unique


def find_installation() -> Installation:
    registry = _registry_products()
    cands = _candidate_dirs(registry)
    for folder, source in cands:
        decap = _find_case_insensitive(folder, "decap.exe")
        if decap is None:
            continue
        recap = _find_case_insensitive(folder, "ReCap.exe")
        version = None
        for prod in registry:
            if prod.get("version"):
                version = prod["version"]
                break
        return Installation(
            found=True,
            install_dir=str(folder),
            decap_exe=str(decap),
            recap_exe=str(recap) if recap else None,
            product_version=version,
            decap_file_version=file_version(decap),
            source=source,
            searched=[str(c) for c, _ in cands],
            registry_products=registry,
        )
    return Installation(found=False, searched=[str(c) for c, _ in cands], registry_products=registry)


def _find_case_insensitive(folder: Path, name: str) -> Path | None:
    direct = folder / name
    if direct.is_file():
        return direct
    try:
        for child in folder.iterdir():
            if child.name.lower() == name.lower() and child.is_file():
                return child
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Execução curta do DeCap (help/version/checkLicense)
# ---------------------------------------------------------------------------

def decode_console(data: bytes) -> str:
    """DeCap escreve no console com a página de código ANSI do Windows."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if IS_WINDOWS:
        try:
            return data.decode("mbcs", errors="replace")
        except LookupError:
            pass
    return data.decode("latin-1", errors="replace")


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_decap_short(launcher: list[str], args: list[str], timeout: int) -> dict[str, Any]:
    """Executa um comando curto do DeCap (sem shell) e devolve saída e mensagens."""
    cmd = [*launcher, *args]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            cwd=str(Path(launcher[-1]).parent) if Path(launcher[-1]).is_file() else None,
            creationflags=_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "timeout": True, "returncode": None, "output": "", "messages": []}
    except OSError as exc:
        raise errors.RecapMcpError(errors.LAUNCH_FAILED, f"Falha ao executar o DeCap: {exc}",
                                   command=cmd) from exc
    text = decode_console(proc.stdout) + "\n" + decode_console(proc.stderr)
    return {
        "ok": True,
        "returncode": proc.returncode,
        "output": text.strip(),
        "messages": parse_messages(text),
    }


@dataclass
class Capabilities:
    help_available: bool
    modes: list[str]
    import_options: list[str]
    version: str | None
    raw_help_excerpt: str | None = None

    def supports(self, option: str) -> bool:
        return option in self.import_options

    def to_dict(self) -> dict[str, Any]:
        return {
            "help_available": self.help_available,
            "modes": self.modes,
            "import_options": self.import_options,
            "decap_version": self.version,
        }


_OPTION_RE = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9]*)")


def parse_help(text: str) -> tuple[list[str], list[str]]:
    flags = set(_OPTION_RE.findall(text or ""))
    modes = [m for m in KNOWN_MODES if m in flags]
    options = [o for o in KNOWN_IMPORT_OPTIONS if o in flags]
    return modes, options


class CapabilityProbe:
    """Consulta (e mantém em cache) ``--help`` e ``--version`` do DeCap instalado."""

    def __init__(self, launcher: list[str]):
        self.launcher = launcher
        self._lock = threading.Lock()
        self._caps: Capabilities | None = None

    def get(self, refresh: bool = False) -> Capabilities:
        with self._lock:
            if self._caps is not None and not refresh:
                return self._caps
            help_res = run_decap_short(self.launcher, ["--help"], HELP_TIMEOUT_S)
            ver_res = run_decap_short(self.launcher, ["--version"], HELP_TIMEOUT_S)
            version = None
            for m in ver_res.get("messages", []):
                if m["kind"] == "M" and re.fullmatch(r"[\d.]+", m.get("text", "")):
                    version = m["text"]
            if help_res.get("ok"):
                modes, options = parse_help(help_res["output"])
                self._caps = Capabilities(True, modes, options, version, help_res["output"][:4000])
            else:
                self._caps = Capabilities(False, [], [], version)
            return self._caps


def check_license(launcher: list[str]) -> dict[str, Any]:
    """Executa ``--checkLicense``. Só é chamada quando o cliente pede explicitamente."""
    res = run_decap_short(launcher, ["--checkLicense"], LICENSE_TIMEOUT_S)
    if not res.get("ok"):
        return {"state": "desconhecido", "reason": "DeCap não respondeu dentro do tempo limite."}
    msgs = res["messages"]
    texts = [m.get("text", "") for m in msgs]
    if any(m["kind"] == "M" and "license check ok" in m.get("text", "").lower() for m in msgs):
        state = "valida"
    elif any(m["kind"] == "E" for m in msgs):
        state = "invalida_ou_indisponivel"
    else:
        state = "desconhecido"
    return {"state": state, "decap_messages": texts, "returncode": res["returncode"]}
