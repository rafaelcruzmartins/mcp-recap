"""Ponto de entrada: ``mcp-recap`` (servidor STDIO) ou ``mcp-recap --diagnose``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def diagnose(check_license: bool) -> int:
    """Imprime um relatório JSON da instalação. Não inicia o servidor MCP."""
    from . import errors, installation
    from .server import Context, _sdk_status

    report: dict = {"server_version": __version__, "python": sys.version.split()[0]}
    inst = installation.find_installation()
    report["installation"] = inst.to_dict()
    report["reality_solutions_sdk"] = _sdk_status(inst)
    ctx = Context()
    report["state_dir"] = str(ctx.state_dir)
    try:
        caps = ctx.capabilities()
        report["decap"] = caps.to_dict()
        if check_license:
            report["license"] = installation.check_license(ctx.decap_launcher())
        else:
            report["license"] = {"state": "desconhecido", "reason": "use --check-license para verificar"}
    except errors.RecapMcpError as exc:
        report["decap"] = exc.to_dict()
    report["jobs"] = ctx.jobs.list(limit=5)
    ok = bool(inst.found and isinstance(report.get("decap"), dict) and report["decap"].get("help_available"))
    report["ready_for_import"] = ok
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-recap", description="Servidor MCP para Autodesk ReCap (STDIO).")
    parser.add_argument("--diagnose", action="store_true", help="mostra diagnóstico em JSON e sai")
    parser.add_argument("--check-license", action="store_true", help="com --diagnose, executa decap --checkLicense")
    parser.add_argument("--version", action="version", version=f"mcp-recap {__version__}")
    args = parser.parse_args(argv)
    if args.diagnose:
        return diagnose(args.check_license)
    from .server import run_stdio

    run_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())
