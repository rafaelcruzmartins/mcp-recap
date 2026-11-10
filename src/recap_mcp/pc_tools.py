"""Ferramentas MCP de leitura de pontos (Plano B: arquivos de origem, sem o Reality Solutions SDK)."""

from __future__ import annotations

import base64
import datetime as _dt
import json
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import errors
from .pointcloud import analysis, export, index, sources

SRC = Annotated[str, Field(description=(
    "Caminho absoluto de um .rcp (usa os arquivos de origem dos scans) ou de um arquivo .e57, .las, .laz, "
    ".pts ou .xyz. Arquivos .rcs não podem ser lidos sem o Reality Solutions SDK."))]
UNIT = Annotated[str, Field(description="Unidade das coordenadas em arquivos de texto (.pts/.xyz): m, mm, cm, in, ft, us_ft.")]
VEC3 = Annotated[list[float] | None, Field(description="[x, y, z] nas coordenadas do arquivo de origem.")]
SAMPLE = Annotated[int, Field(ge=0, le=analysis.MAX_SAMPLE, description="Pontos na amostra devolvida (máx. 10 000).")]
COLOR = Annotated[str, Field(description="rgb, intensity ou elevation (usa elevação se o atributo não existir).")]
WIDTH = Annotated[int, Field(ge=128, le=4096, description="Largura da área de desenho, em pixels.")]

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_NEW = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _with_image(ctx, out: dict[str, Any], tool: str) -> CallToolResult:
    png = out.pop("png")
    folder = Path(ctx.state_dir) / "imagens"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{_dt.datetime.now():%Y%m%d-%H%M%S-%f}-{tool}.png"
    f.write_bytes(png)
    out["image"]["file"] = str(f)
    text = json.dumps(out, ensure_ascii=False, default=_json_default)
    return CallToolResult(
        content=[TextContent(type="text", text=text),
                 ImageContent(type="image", data=base64.b64encode(png).decode("ascii"), mimeType="image/png")],
        structuredContent=json.loads(text),
    )


def _json_default(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def _clean(out: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(out, ensure_ascii=False, default=_json_default))


def register(mcp, ctx, tool_wrapper) -> None:
    def cloud(source: str, input_unit: str) -> index.CloudIndex:
        return index.open_cloud(ctx.state_dir, source, input_unit)

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def point_cloud_info(source: SRC, input_unit: UNIT = "m") -> dict[str, Any]:
        """Resumo da nuvem: pontos por scan, limites, atributos, unidade, sistema de coordenadas, transformações scan→arquivo e estado do índice."""
        srcs, context = sources.resolve_sources(source, input_unit)
        key = index.cache_key(srcs, input_unit)
        folder = index.cache_root(ctx.state_dir) / key
        if (folder / "manifest.json").is_file():
            out = analysis.info(index.CloudIndex(folder))
            out["indexed"] = True
        else:
            out = {"ok": True, "indexed": False, "sources": [s.to_dict() for s in srcs],
                   "estimated_points": index.estimate_points(srcs),
                   "note": ("Limites e contagem vêm dos cabeçalhos dos arquivos. As consultas indexam automaticamente "
                            f"nuvens de até {index.SYNC_INDEX_MAX_POINTS:,} pontos; acima disso, rode index_point_cloud.")}
        out["project_context"] = context
        out["reads_from"] = "arquivos de origem (Plano B); os .rcs não são decodificados"
        return _clean(out)

    @mcp.tool(annotations=WRITE_NEW)
    @tool_wrapper
    def index_point_cloud(source: SRC, input_unit: UNIT = "m",
                          tile_size_m: Annotated[float | None, Field(gt=0, le=1000, description="Tamanho do bloco XY; automático se omitido.")] = None
                          ) -> dict[str, Any]:
        """Indexa a nuvem em blocos (cache local, sem alterar os originais). Nuvens grandes viram job assíncrono."""
        srcs, context = sources.resolve_sources(source, input_unit)
        key = index.cache_key(srcs, input_unit)
        folder = index.cache_root(ctx.state_dir) / key
        if (folder / "manifest.json").is_file():
            return _clean({**analysis.info(index.CloudIndex(folder)), "already_indexed": True})
        est = index.estimate_points(srcs)
        if est <= index.SYNC_INDEX_MAX_POINTS:
            f = index.build_index(ctx.state_dir, srcs, context, input_unit, tile_size_m=tile_size_m)
            return _clean({**analysis.info(index.CloudIndex(f)), "built_now": True})

        def task(progress, cancel, log):
            log(f"indexando {len(srcs)} arquivo(s), ~{est:,} pontos")
            f = index.build_index(ctx.state_dir, srcs, context, input_unit, progress=progress,
                                  cancelled=cancel.is_set, tile_size_m=tile_size_m)
            return _clean(analysis.info(index.CloudIndex(f)))

        job = ctx.jobs.start_task("index_point_cloud", {"source": source, "estimated_points": est}, task)
        return {"ok": True, "message": "Indexação iniciada. Acompanhe com get_job_status.", **job}

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def query_box(source: SRC, min: VEC3 = None, max: VEC3 = None, sample_size: SAMPLE = analysis.DEFAULT_SAMPLE,
                  input_unit: UNIT = "m") -> dict[str, Any]:
        """Contagem, limites reais, estatísticas de Z e amostra decimada dos pontos dentro de uma caixa."""
        return _clean(analysis.query_box(cloud(source, input_unit), min, max, sample_size))

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def z_histogram(source: SRC, min: VEC3 = None, max: VEC3 = None,
                    bin_size_m: Annotated[float, Field(gt=0, le=1000)] = 0.05, input_unit: UNIT = "m") -> dict[str, Any]:
        """Histograma de Z numa região, com picos candidatos a pisos, lajes e forros."""
        return _clean(analysis.z_histogram(cloud(source, input_unit), min, max, bin_size_m))

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def section(source: SRC,
                mode: Annotated[str, Field(description="horizontal (cota + espessura) ou vertical (plano por p1→p2).")],
                thickness_m: Annotated[float, Field(gt=0, le=100)],
                z: Annotated[float | None, Field(description="Cota do corte horizontal.")] = None,
                p1: Annotated[list[float] | None, Field(description="[x, y] início do corte vertical.")] = None,
                p2: Annotated[list[float] | None, Field(description="[x, y] fim do corte vertical.")] = None,
                min: VEC3 = None, max: VEC3 = None, sample_size: SAMPLE = analysis.DEFAULT_SAMPLE,
                color_by: COLOR = "elevation", width_px: WIDTH = 1200, input_unit: UNIT = "m") -> Any:
        """Corte horizontal ou vertical: pontos 2D decimados e imagem PNG da fatia."""
        out = analysis.section(cloud(source, input_unit), mode, thickness_m, z=z, p1=p1, p2=p2, bmin=min, bmax=max,
                               sample_size=sample_size, color_by=color_by, width_px=width_px)
        return _with_image(ctx, out, "secao")

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def render_view(source: SRC,
                    view: Annotated[str, Field(description="top, bottom, front, back, left, right ou custom.")] = "top",
                    direction: Annotated[list[float] | None, Field(description="Com view=custom: direção de visada [dx, dy, dz].")] = None,
                    min: VEC3 = None, max: VEC3 = None, color_by: COLOR = "rgb", width_px: WIDTH = 1200,
                    point_size: Annotated[int, Field(ge=1, le=5)] = 1, input_unit: UNIT = "m") -> Any:
        """Imagem PNG ortográfica de uma região, com eixos em coordenadas, escala gráfica e legenda."""
        out = analysis.render_view(cloud(source, input_unit), view, direction, min, max, color_by, width_px, point_size)
        return _with_image(ctx, out, "vista")

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def profile(source: SRC,
                polyline: Annotated[list[list[float]], Field(description="Vértices [[x, y], ...] (2 a 500).")],
                width_m: Annotated[float, Field(gt=0, le=1000, description="Largura total da faixa.")],
                step_m: Annotated[float, Field(gt=0, description="Intervalo das estatísticas por estaca.")] = 0.5,
                zmin: float | None = None, zmax: float | None = None, sample_size: SAMPLE = analysis.DEFAULT_SAMPLE,
                width_px: WIDTH = 1200, input_unit: UNIT = "m") -> Any:
        """Perfil ao longo de uma polilinha: Z mínimo, mediano e máximo por estaca, pontos 2D e imagem."""
        out = analysis.profile(cloud(source, input_unit), polyline, width_m, step_m, sample_size, zmin, zmax, width_px)
        return _with_image(ctx, out, "perfil")

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def nearest_points(source: SRC, point: Annotated[list[float], Field(description="[x, y, z]")],
                       k: Annotated[int, Field(ge=1, le=100)] = 10,
                       max_radius_m: Annotated[float, Field(gt=0, le=100)] = 0.5, input_unit: UNIT = "m") -> dict[str, Any]:
        """Pontos mais próximos de uma coordenada, dentro de um raio máximo."""
        return _clean(analysis.nearest_points(cloud(source, input_unit), point, k, max_radius_m))

    @mcp.tool(annotations=READ)
    @tool_wrapper
    def measure(source: SRC, p1: Annotated[list[float], Field(description="[x, y, z]")],
                p2: Annotated[list[float], Field(description="[x, y, z]")],
                snap: Annotated[bool, Field(description="Aproxima cada extremidade do ponto mais próximo da nuvem.")] = True,
                snap_radius_m: Annotated[float, Field(gt=0, le=10)] = 0.05, input_unit: UNIT = "m") -> dict[str, Any]:
        """Distância 3D e horizontal entre dois pontos, com opção de encaixar nos pontos da nuvem."""
        return _clean(analysis.measure(cloud(source, input_unit), p1, p2, snap, snap_radius_m))

    @mcp.tool(annotations=WRITE_NEW)
    @tool_wrapper
    def export_subset(source: SRC,
                      output_folder: Annotated[str, Field(description="Pasta de destino (absoluta).")],
                      file_name: Annotated[str, Field(description="Nome do arquivo novo (sem sobrescrever).")],
                      format: Annotated[str, Field(description="pts, xyz ou e57.")] = "pts",
                      min: VEC3 = None, max: VEC3 = None,
                      section_mode: Annotated[str | None, Field(description="Opcional: horizontal ou vertical.")] = None,
                      z: float | None = None, thickness_m: float | None = None,
                      p1: list[float] | None = None, p2: list[float] | None = None,
                      input_unit: UNIT = "m") -> dict[str, Any]:
        """Exporta a região (caixa ou seção) para um arquivo novo .pts/.xyz/.e57, como job assíncrono."""
        c = cloud(source, input_unit)
        region = _export_region(c, min, max, section_mode, z, thickness_m, p1, p2)
        target = export.plan_export(output_folder, file_name, format)

        def task(progress, cancel, log):
            return _clean(export.run_export(c, region, target, format, progress, cancel.is_set, log))

        job = ctx.jobs.start_task("export_subset", {"source": source, "output": str(target), "format": format}, task)
        return {"ok": True, "message": "Exportação iniciada. Acompanhe com get_job_status.", "output": str(target),
                "coordinate_frame": analysis.frame_info(c), **job}


def _export_region(c, bmin, bmax, mode, z, thickness, p1, p2) -> index.Region:
    base = analysis.box_region(c, bmin, bmax)
    if mode is None:
        return base
    if thickness is None:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Seções exigem thickness_m.")
    t = analysis._positive(thickness, "thickness_m", 100)
    if mode == "horizontal":
        if z is None:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Seção horizontal exige z.")
        return index.Region(np.r_[base.min[:2], z - t / 2], np.r_[base.max[:2], z + t / 2])
    if mode == "vertical":
        a, b = analysis._vec(p1, 2, "p1"), analysis._vec(p2, 2, "p2")
        d = b - a
        L = float(np.hypot(*d))
        if L == 0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "p1 e p2 devem ser diferentes.")
        dv = d / L
        nv = np.array([-dv[1], dv[0]])
        cs = np.array([a + nv * t / 2, a - nv * t / 2, b + nv * t / 2, b - nv * t / 2])

        def pred(x, y, zz):
            rx, ry = x - a[0], y - a[1]
            s = rx * dv[0] + ry * dv[1]
            tol = index.EDGE_TOL
            return (s >= -tol) & (s <= L + tol) & (np.abs(rx * nv[0] + ry * nv[1]) <= t / 2 + tol)

        return index.Region(np.r_[cs.min(axis=0), base.min[2]], np.r_[cs.max(axis=0), base.max[2]], pred)
    raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "section_mode deve ser horizontal ou vertical.")
