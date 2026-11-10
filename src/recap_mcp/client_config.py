"""Registro do servidor nos clientes MCP, preservando as demais configurações.

Uso:  uv run mcp-recap-configurar --client claude-desktop [--client antigravity] [--dry-run]

Regras:
* Só a entrada ``recap`` (ou ``--name``) é criada ou substituída; outros servidores ficam intactos.
* Antes de alterar um arquivo existente, é criado ``<arquivo>.bak-AAAAMMDD-HHMMSS`` ao lado.
* JSON é gravado em UTF-8 sem BOM, de forma atômica, e relido para validação.
* Arquivo existente inválido não é alterado: o script para com erro.
* Claude Code mantém a própria configuração; o script só executa ``claude mcp add`` com ``--run``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

CLIENTS = ("claude-desktop", "antigravity", "codex", "claude-code")


class ConfigError(Exception):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def server_entry(uv: str, repo: Path) -> dict[str, Any]:
    return {"command": uv, "args": ["--directory", str(repo), "run", "mcp-recap"]}


def default_paths(client: str) -> list[Path]:
    home = Path.home()
    if client == "claude-desktop":
        paths = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # versões recentes do app guardam a configuração por conta em Claude-Data\claude_<id>\
            paths.extend(sorted(Path(local, "Claude-Data").glob("*/claude_desktop_config.json")))
            # instalação MSIX/Microsoft Store
            for pkg in sorted(Path(local, "Packages").glob("Claude_*")):
                paths.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
        return paths
    if client == "antigravity":
        return [home / ".gemini" / "config" / "mcp_config.json"]
    if client == "codex":
        return [home / ".codex" / "config.toml"]
    return []


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    shutil.copy2(path, dest)
    return dest


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-mcp-recap")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def update_json(path: Path, name: str, entry: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if path.exists():
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig") if raw.strip() else "{}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: JSON inválido ({exc}); nada foi alterado.") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: a raiz do JSON não é um objeto; nada foi alterado.")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: 'mcpServers' não é um objeto; nada foi alterado.")
    previous = servers.get(name)
    servers[name] = entry
    result = {"path": str(path), "changed": previous != entry, "replaced": previous is not None,
              "other_servers": sorted(k for k in servers if k != name), "backup": None, "dry_run": dry_run}
    if dry_run or not result["changed"]:
        return result
    result["backup"] = str(b) if (b := _backup(path)) else None
    _atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    json.loads(path.read_text(encoding="utf-8"))  # validação
    return result


_TOML_HEADER = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(#.*)?$")


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # string básica TOML válida


def codex_block(name: str, entry: dict[str, Any]) -> str:
    args = ", ".join(_toml_str(a) for a in entry["args"])
    return (f"[mcp_servers.{name}]\n"
            f"command = {_toml_str(entry['command'])}\n"
            f"args = [{args}]\n"
            "startup_timeout_sec = 60\n"
            "tool_timeout_sec = 300\n")


def update_codex(path: Path, name: str, entry: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    text = ""
    if path.exists():
        text = path.read_bytes().decode("utf-8-sig")
        try:
            before = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: TOML inválido ({exc}); nada foi alterado.") from exc
    else:
        before = {}
    prefix = f"mcp_servers.{name}"
    out: list[str] = []
    skipping = False
    replaced = False
    for line in text.splitlines(keepends=True):
        m = _TOML_HEADER.match(line)
        if m:
            table = m.group(1).strip().strip('"')
            skipping = table == prefix or table.startswith(prefix + ".")
            replaced = replaced or skipping
        if not skipping:
            out.append(line)
    new_text = "".join(out).rstrip("\n")
    new_text = (new_text + "\n\n" if new_text else "") + codex_block(name, entry)
    after = tomllib.loads(new_text)
    other_before = {k: v for k, v in before.get("mcp_servers", {}).items() if k != name}
    other_after = {k: v for k, v in after.get("mcp_servers", {}).items() if k != name}
    if other_before != other_after or {k: v for k, v in before.items() if k != "mcp_servers"} != \
            {k: v for k, v in after.items() if k != "mcp_servers"}:
        raise ConfigError(f"{path}: a edição alteraria outras configurações; nada foi alterado.")
    changed = new_text != text
    result = {"path": str(path), "changed": changed, "replaced": replaced, "other_servers": sorted(other_after),
              "backup": None, "dry_run": dry_run}
    if dry_run or not changed:
        return result
    result["backup"] = str(b) if (b := _backup(path)) else None
    _atomic_write(path, new_text.encode("utf-8"))
    tomllib.loads(path.read_text(encoding="utf-8"))
    return result


def claude_code_command(name: str, entry: dict[str, Any], claude: str = "claude") -> list[str]:
    return [claude, "mcp", "add", "--scope", "user", name, "--", entry["command"], *entry["args"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcp-recap-configurar",
                                 description="Registra o servidor MCP do ReCap nos clientes, com backup.")
    ap.add_argument("--client", action="append", choices=CLIENTS, required=True)
    ap.add_argument("--config", type=Path, help="caminho do arquivo de configuração (substitui o padrão)")
    ap.add_argument("--name", default="recap")
    ap.add_argument("--uv", help="caminho do uv.exe (padrão: o encontrado no PATH)")
    ap.add_argument("--repo", type=Path, default=repo_root())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true", help="claude-code: executa 'claude mcp add' em vez de só mostrar")
    a = ap.parse_args(argv)

    uv = a.uv or shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    if not Path(uv).is_file():
        print(json.dumps({"ok": False, "error": f"uv não encontrado ({uv}); use --uv"}, ensure_ascii=False))
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", a.name):
        print(json.dumps({"ok": False, "error": "--name inválido"}))
        return 2
    entry = server_entry(str(Path(uv).resolve()), a.repo.resolve())
    report: list[dict[str, Any]] = []
    code = 0
    for client in a.client:
        try:
            if client == "claude-code":
                cmd = claude_code_command(a.name, entry)
                item: dict[str, Any] = {"client": client, "command": cmd}
                if a.run and not a.dry_run:
                    exe = shutil.which("claude")
                    if not exe:
                        raise ConfigError("CLI 'claude' não encontrada no PATH.")
                    cmd[0] = exe
                    subprocess.run([exe, "mcp", "remove", "--scope", "user", a.name], capture_output=True, check=False)
                    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    item.update(returncode=proc.returncode, output=(proc.stdout + proc.stderr).strip())
                    code = code or proc.returncode
                report.append(item)
                continue
            paths = [a.config] if a.config else default_paths(client)
            if client == "claude-desktop" and not a.config:
                existing = [p for p in paths if p.exists()]
                paths = existing or paths[:1]
            for p in paths:
                fn = update_codex if client == "codex" else update_json
                report.append({"client": client, **fn(p, a.name, entry, dry_run=a.dry_run)})
        except ConfigError as exc:
            report.append({"client": client, "error": str(exc)})
            code = 1
    print(json.dumps({"ok": code == 0, "entry": entry, "results": report}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
