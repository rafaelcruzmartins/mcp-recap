"""Abertura de um projeto existente na interface do ReCap.

Observado no ReCap 27.0.1.242:
* ``ReCap.exe "<projeto.rcp>"`` (mesmo formato da associação de arquivos do Windows)
  abre o projeto, mas a inicialização pode levar 1 a 2 minutos.
* Iniciado com ``DETACHED_PROCESS`` o ReCap encerra antes de criar a janela; por isso
  essa flag não é usada.
* O ReCap registra ``[POINTCLOUD] Project loading success - <caminho>`` em
  ``%LOCALAPPDATA%\\Autodesk\\Autodesk ReCap\\AutodeskReCap.log``. Esse registro é a
  única confirmação de carregamento disponível sem automação de interface.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import errors

LOAD_RE = re.compile(r"\[POINTCLOUD\] Project loading (success|fail\w*) - (.+?)\"?\s*$", re.IGNORECASE)
MAX_WAIT_S = 300


def recap_log_path() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "Autodesk" / "Autodesk ReCap" / "AutodeskReCap.log"


def _norm(p: str) -> str:
    return p.strip().strip('"').replace("/", "\\").lower()


def scan_log_for_load(log: Path, offset: int, project: Path) -> str | None:
    """Procura, a partir de ``offset``, o resultado do carregamento de ``project``."""
    try:
        with log.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(4 * 1024 * 1024)
    except OSError:
        return None
    target = _norm(str(project))
    for line in data.decode("utf-8", errors="replace").splitlines():
        m = LOAD_RE.search(line)
        if m and _norm(m.group(2)) == target:
            return m.group(1).lower()
    return None


def running_recap_pids() -> list[int]:
    """PIDs de ReCap.exe em execução (somente leitura, via tasklist)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ReCap.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=30, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "recap.exe" and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def open_project(recap_exe: str, project: Path, wait_for_load_seconds: int = 0,
                 sleep=time.sleep, popen=subprocess.Popen, allow_second_instance: bool = False,
                 running=None) -> dict[str, Any]:
    if not (0 <= wait_for_load_seconds <= MAX_WAIT_S):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"wait_for_load_seconds deve estar entre 0 e {MAX_WAIT_S}.")
    already = (running or running_recap_pids)()
    if already and not allow_second_instance:
        # Observado no ReCap 27.0.1.242: com outra instância aberta, a nova não carregou o projeto em 240 s.
        raise errors.RecapMcpError(
            errors.RECAP_ALREADY_RUNNING,
            "O ReCap já está aberto. Abra o projeto pelo menu do ReCap aberto, ou feche-o e chame de novo. "
            "Para iniciar mesmo assim uma segunda instância, use allow_second_instance=true.",
            running_pids=already)
    log = recap_log_path()
    offset = log.stat().st_size if log and log.is_file() else 0
    try:
        proc = popen(
            [recap_exe, str(project)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(recap_exe).parent),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except OSError as exc:
        raise errors.RecapMcpError(errors.LAUNCH_FAILED, f"Falha ao abrir o ReCap: {exc}") from exc

    result: dict[str, Any] = {
        "ok": True,
        "path": str(project),
        "recap_exe": recap_exe,
        "pid": proc.pid,
        "load_confirmed": None,
        "note": ("ReCap iniciado. O servidor não acompanha nem encerra este processo. A inicialização do "
                 "ReCap pode levar 1 a 2 minutos."),
    }
    if wait_for_load_seconds <= 0:
        return result
    if log is None:
        result["load_check"] = "log do ReCap indisponível; carregamento não verificado"
        return result
    waited = 0
    while waited < wait_for_load_seconds:
        sleep(min(3, wait_for_load_seconds - waited))
        waited += 3
        outcome = scan_log_for_load(log, offset, project) if log.is_file() else None
        if outcome:
            result["load_confirmed"] = outcome == "success"
            result["load_check"] = f"AutodeskReCap.log registrou: Project loading {outcome}"
            return result
        if proc.poll() is not None:
            result["load_confirmed"] = False
            result["load_check"] = f"o processo do ReCap terminou com código {proc.returncode} antes de carregar"
            return result
    result["load_check"] = (f"nenhum registro de carregamento em {wait_for_load_seconds} s; o ReCap pode ainda "
                            "estar iniciando")
    return result
