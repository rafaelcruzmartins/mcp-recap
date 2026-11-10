"""Servidor MCP (STDIO) para Autodesk ReCap.

STDOUT é exclusivo do protocolo MCP. Logs vão para STDERR e para
``<estado>/logs/server.log``.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__, errors, installation, projects, recap_app
from .decap import (
    INPUT_UNITS,
    LISTED_INPUT_EXTENSIONS,
    VERIFIED_INPUT_EXTENSIONS,
    plan_import,
)
from .jobs import JobManager
from .paths import existing_file

log = logging.getLogger("recap_mcp")


def default_state_dir() -> Path:
    env = os.environ.get("RECAP_MCP_STATE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "mcp-recap"


class Context:
    """Estado compartilhado do servidor (instalação, capacidades, jobs)."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or default_state_dir())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._install: installation.Installation | None = None
        self._probe: installation.CapabilityProbe | None = None
        self._lock = threading.Lock()
        max_jobs = int(os.environ.get("RECAP_MCP_MAX_JOBS", "2") or 2)
        self.jobs = JobManager(self.state_dir, self.decap_launcher, max_concurrent=max(1, min(max_jobs, 8)))

    def install(self, refresh: bool = False) -> installation.Installation:
        with self._lock:
            if self._install is None or refresh:
                self._install = installation.find_installation()
                self._probe = None
            return self._install

    def decap_launcher(self) -> list[str]:
        """Comando-base do DeCap. ``RECAP_MCP_DECAP_LAUNCHER`` existe só para testes automatizados."""
        override = os.environ.get("RECAP_MCP_DECAP_LAUNCHER")
        if override:
            cmd = json.loads(override)
            if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
                raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "RECAP_MCP_DECAP_LAUNCHER deve ser lista JSON.")
            return cmd
        inst = self.install()
        if not inst.found or not inst.decap_exe:
            raise errors.RecapMcpError(
                errors.DECAP_NOT_FOUND,
                "decap.exe não encontrado. Instale o Autodesk ReCap Pro ou defina RECAP_HOME com a pasta do decap.exe.",
                searched=inst.searched)
        return [inst.decap_exe]

    def capabilities(self, refresh: bool = False) -> installation.Capabilities:
        launcher = self.decap_launcher()
        with self._lock:
            if self._probe is None or self._probe.launcher != launcher:
                self._probe = installation.CapabilityProbe(launcher)
            probe = self._probe
        return probe.get(refresh=refresh)


def _error_result(body: dict[str, Any]) -> CallToolResult:
    """Resultado MCP com ``isError=true``, texto JSON e o mesmo corpo como conteúdo estruturado."""
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
        structuredContent=body,
    )


def _tool(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Converte erros em resultado MCP de erro com corpo ``{"ok": false, "error": {code, message, details}}``."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            # executa fora do laço de eventos: chamadas lentas não bloqueiam outras requisições
            return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
        except errors.RecapMcpError as exc:
            log.info("%s: %s %s", fn.__name__, exc.code, exc.message)
            return _error_result(exc.to_dict())
        except Exception as exc:
            log.exception("erro inesperado em %s", fn.__name__)
            return _error_result({"ok": False, "error": {"code": "internal_error",
                                                         "message": f"{type(exc).__name__}: {exc}"}})

    return wrapper


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def build_server(ctx: Context | None = None) -> tuple[FastMCP, Context]:
    ctx = ctx or Context()
    mcp = FastMCP(
        "recap",
        instructions=(
            "Ferramentas para Autodesk ReCap no Windows. Comece por check_recap_installation. "
            "Leitura: find_projects, read_project, list_scans (metadados; nenhum ponto é devolvido). "
            "Importação: import_scans cria SEMPRE um projeto novo via DeCap.exe e devolve um job_id; "
            "acompanhe com get_job_status. Use caminhos absolutos. "
            "Pontos: point_cloud_info, query_box, z_histogram, section, render_view, profile, nearest_points, "
            "measure e export_subset leem os arquivos de origem (.e57/.las/.laz/.pts/.xyz, ou o .rcp que aponta "
            "para eles), nunca devolvem a nuvem inteira e informam o sistema das coordenadas. Nuvens grandes "
            "precisam de index_point_cloud (job) antes das consultas."
        ),
    )

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def check_recap_installation(
        check_license: Annotated[bool, Field(description="Executa 'decap.exe --checkLicense' (alguns segundos).")] = False,
        refresh: Annotated[bool, Field(description="Refaz a busca da instalação e a leitura do --help.")] = False,
    ) -> dict[str, Any]:
        """Detecta o ReCap instalado, versão, caminhos, opções do DeCap disponíveis e, se pedido, a licença."""
        inst = ctx.install(refresh=refresh)
        result: dict[str, Any] = {
            "ok": True,
            "server_version": __version__,
            "installation": inst.to_dict(),
            "state_dir": str(ctx.state_dir),
            "supported_input_extensions": {
                "verified": sorted(VERIFIED_INPUT_EXTENSIONS),
                "listed_not_verified": sorted(LISTED_INPUT_EXTENSIONS),
            },
            "input_units": list(INPUT_UNITS),
            "limitations": [
                "Leitura de RCP: somente metadados do manifesto XML; formato não documentado pela Autodesk.",
                "Leitura de RCS: somente cabeçalho; pontos não são decodificados (exige Reality Solutions SDK).",
                "DeCap cria projetos novos; não edita projetos existentes nem controla a interface do ReCap.",
                "A Reality Capture API na nuvem não é usada; nenhum dado sai da máquina.",
            ],
        }
        sdk = _sdk_status(inst)
        result["reality_solutions_sdk"] = sdk
        if not inst.found and not os.environ.get("RECAP_MCP_DECAP_LAUNCHER"):
            result["license"] = {"state": "desconhecido", "reason": "ReCap não encontrado."}
            result["decap"] = None
            return result
        caps = ctx.capabilities(refresh=refresh)
        result["decap"] = caps.to_dict()
        if check_license:
            result["license"] = installation.check_license(ctx.decap_launcher())
        else:
            result["license"] = {
                "state": "desconhecido",
                "reason": "Não verificado. Chame com check_license=true para executar 'decap.exe --checkLicense'.",
            }
        return result

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def find_projects(
        folder: Annotated[str, Field(description="Pasta absoluta onde procurar.")],
        include_rcs: Annotated[bool, Field(description="Inclui arquivos .rcs além de .rcp.")] = True,
        max_depth: Annotated[int, Field(ge=0, le=32, description="Profundidade máxima de subpastas.")] = 6,
        max_results: Annotated[int, Field(ge=1, le=5000)] = 200,
    ) -> dict[str, Any]:
        """Localiza projetos .rcp e scans .rcs dentro de uma pasta, com limites de busca."""
        return projects.find_projects(folder, include_rcs=include_rcs, max_depth=max_depth, max_results=max_results)

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def read_project(
        path: Annotated[str, Field(description="Caminho absoluto de um .rcp ou .rcs.")],
    ) -> dict[str, Any]:
        """Lê metadados confiáveis de um projeto .rcp (manifesto) ou o cabeçalho de um .rcs."""
        return projects.read_project(path)

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def list_scans(
        path: Annotated[str, Field(description="Caminho absoluto do projeto .rcp.")],
        verify_rcs_headers: Annotated[bool, Field(description="Confere o cabeçalho de cada .rcs encontrado.")] = True,
        max_scans: Annotated[int, Field(ge=1, le=5000)] = 1000,
    ) -> dict[str, Any]:
        """Lista os scans do projeto, os arquivos .rcs referenciados e indica os ausentes."""
        return projects.list_scans(path, verify_rcs_headers=verify_rcs_headers, max_scans=max_scans)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                          openWorldHint=False))
    @_tool
    def import_scans(
        inputs: Annotated[list[str], Field(description="Arquivos de scan (caminhos absolutos).")],
        output_folder: Annotated[str, Field(description="Pasta de destino (absoluta). Criada se não existir.")],
        project_name: Annotated[str, Field(description="Nome do novo projeto, sem extensão.")],
        decimation_mm: Annotated[float | None, Field(description="--decimation: distância mínima entre pontos, em mm.")] = None,
        min_range_m: Annotated[float | None, Field(description="--minRangeClipping: distância mínima ao scanner; inteiro (unidade não documentada pela Autodesk, presumivelmente metros).")] = None,
        max_range_m: Annotated[float | None, Field(description="--maxRangeClipping: distância máxima ao scanner; inteiro (unidade não documentada pela Autodesk, presumivelmente metros).")] = None,
        unify: Annotated[bool, Field(description="--unify: unifica os scans em um só.")] = False,
        normalize_intensity: Annotated[bool | None, Field(description="--normalizeIntensity 0|1.")] = None,
        input_unit: Annotated[str | None, Field(description="--inputUnitType: mm, cm, m, in, ft ou us_ft.")] = None,
        current_coordinate_system: Annotated[str | None, Field(description="--currentCoordinateSystem (código Autodesk).")] = None,
        target_coordinate_system: Annotated[str | None, Field(description="--targetCoordinateSystem (código Autodesk).")] = None,
        e57_enable_image_import: bool = False,
        e57_create_scan_per_image: bool = False,
        e57_match_image_resolution: bool = False,
        e57_level_image: bool = False,
        dry_run: Annotated[bool, Field(description="Só valida e mostra o comando, sem executar.")] = False,
    ) -> dict[str, Any]:
        """Cria um projeto ReCap NOVO com DeCap.exe (--importWithLicense). Retorna um job_id assíncrono."""
        caps = ctx.capabilities()
        plan = plan_import(
            caps, output_folder, project_name, inputs,
            decimation_mm=decimation_mm, min_range_m=min_range_m, max_range_m=max_range_m, unify=unify,
            normalize_intensity=normalize_intensity, input_unit=input_unit,
            current_coordinate_system=current_coordinate_system, target_coordinate_system=target_coordinate_system,
            e57_enable_image_import=e57_enable_image_import, e57_create_scan_per_image=e57_create_scan_per_image,
            e57_match_image_resolution=e57_match_image_resolution, e57_level_image=e57_level_image,
        )
        if dry_run:
            return {"ok": True, "dry_run": True, "plan": plan.to_dict(),
                    "command": [*ctx.decap_launcher(), *plan.build_args()]}
        job = ctx.jobs.start_import(plan)
        return {"ok": True, "message": "Importação iniciada. Acompanhe com get_job_status.", **job}

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def get_job_status(
        job_id: str,
        log_lines: Annotated[int, Field(ge=0, le=200, description="Linhas finais do log do DeCap.")] = 40,
    ) -> dict[str, Any]:
        """Estado, horários, código de saída, log limitado e verificação dos arquivos de saída de um job."""
        return ctx.jobs.status(job_id, log_lines=log_lines)

    @mcp.tool(annotations=READ_ONLY)
    @_tool
    def list_jobs(
        status: Annotated[str | None, Field(description="Filtra por queued, running, succeeded, failed, cancelled ou interrupted.")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """Lista os jobs criados por este servidor (inclusive de execuções anteriores)."""
        return ctx.jobs.list(status=status, limit=limit)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                                          openWorldHint=False))
    @_tool
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancela um job ativo iniciado por esta instância do servidor. Não afeta outros processos."""
        return ctx.jobs.cancel(job_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                          openWorldHint=False))
    @_tool
    def open_project_in_recap(
        path: Annotated[str, Field(description="Caminho absoluto de um .rcp existente.")],
        wait_for_load_seconds: Annotated[int, Field(ge=0, le=300, description=(
            "Se > 0, aguarda até este tempo pelo registro de carregamento no log do ReCap."))] = 0,
        allow_second_instance: Annotated[bool, Field(description=(
            "Inicia outra instância mesmo com o ReCap já aberto (nos testes, ela não carregou o projeto)."))] = False,
    ) -> dict[str, Any]:
        """Abre um projeto .rcp existente na interface do ReCap (chamada explícita). Não altera o arquivo."""
        p = existing_file(path, "path")
        if p.suffix.lower() != ".rcp":
            raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT, "Somente arquivos .rcp podem ser abertos.")
        projects.load_manifest(p)  # recusa arquivos que não são projetos válidos
        inst = ctx.install()
        if not inst.recap_exe:
            raise errors.RecapMcpError(errors.RECAP_NOT_FOUND, "ReCap.exe não encontrado.")
        return recap_app.open_project(inst.recap_exe, p, wait_for_load_seconds=wait_for_load_seconds,
                                      allow_second_instance=allow_second_instance)

    from . import pc_tools

    pc_tools.register(mcp, ctx, _tool)
    return mcp, ctx


def _sdk_status(inst: installation.Installation) -> dict[str, Any]:
    """O Reality Solutions SDK é distribuído pela ADN e não acompanha o ReCap."""
    found = []
    if inst.install_dir:
        for name in ("AdskRealityStudioSDK.dll", "RCProjectSDK.dll", "AdskRcProject.dll"):
            if (Path(inst.install_dir) / name).is_file():
                found.append(name)
    return {
        "available": False,
        "files_found_in_install_dir": found,
        "note": ("Integração com o Reality Solutions SDK não implementada: o SDK é obtido pela Autodesk Developer "
                 "Network (ADN) e não acompanha o ReCap. Sem ele, os pontos de arquivos .rcs não são lidos."),
    }


def setup_logging(state_dir: Path) -> None:
    root = logging.getLogger("recap_mcp")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    root.propagate = False  # o FastMCP também configura o logger raiz; evita linhas duplicadas
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(fmt)
    root.addHandler(err)
    try:
        logs = state_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logs / "server.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def run_stdio() -> None:
    state = default_state_dir()
    setup_logging(state)
    mcp, ctx = build_server(Context(state))
    log.info("mcp-recap %s iniciando (estado em %s)", __version__, state)
    try:
        mcp.run(transport="stdio")
    finally:
        ctx.jobs.shutdown()
