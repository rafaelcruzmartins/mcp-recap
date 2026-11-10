"""Ferramentas MCP para ver e navegar na nuvem de pontos (leitura direta de .rcp/.rcs)."""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any, Literal

import numpy as np
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from .paths import existing_file
from .viewer import Viewer

VIEW = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False)

ColorBy = Annotated[Literal["auto", "rgb", "intensity", "elevation"], Field(description=(
    "Cor dos pontos: auto (RGB se houver, senão intensidade), rgb, intensity ou elevation (rampa por cota)."))]
Width = Annotated[int, Field(ge=320, le=1600, description="Largura da imagem em pixels.")]
Height = Annotated[int, Field(ge=240, le=1200, description="Altura da imagem em pixels.")]
MaxPoints = Annotated[int, Field(ge=50_000, le=8_000_000, description=(
    "Orçamento de pontos lidos do disco para esta imagem. Mais pontos: mais detalhe e mais tempo."))]
PointScale = Annotated[float, Field(ge=0.3, le=4.0, description="Multiplicador do tamanho dos pontos.")]
PixelGrid = Annotated[bool, Field(description=(
    "Sobrepõe uma grade com as coordenadas de pixel, útil antes de pick_point / go_to_pixel."))]


def _image_result(jpeg: bytes, meta: dict[str, Any]) -> CallToolResult:
    body = {"ok": True, **meta}
    return CallToolResult(content=[
        ImageContent(type="image", data=base64.b64encode(jpeg).decode("ascii"), mimeType="image/jpeg"),
        TextContent(type="text", text=json.dumps(body, ensure_ascii=False)),
    ])


def register(mcp: FastMCP, tool: Any) -> Viewer:
    """Registra as ferramentas de visualização. ``tool`` é o decorador de erros do servidor."""
    viewer = Viewer()

    @mcp.tool(annotations=VIEW)
    @tool
    def open_cloud(
        path: Annotated[str, Field(description="Caminho absoluto de um projeto .rcp ou de um scan .rcs.")],
        width: Width = 1024,
        max_points: MaxPoints = 1_500_000,
    ) -> CallToolResult:
        """Abre uma nuvem de pontos do ReCap (.rcp ou .rcs) para ver e navegar, lendo os pontos direto do
        arquivo (não precisa do ReCap aberto nem de exportar). Devolve os limites, o número de pontos e
        uma imagem de vista geral. Depois use view_cloud, move_camera, orbit_camera, go_to_pixel,
        plan_view e section_view."""
        p = existing_file(path, "path")
        with viewer.lock:
            info = viewer.open(p)
            jpeg, meta = viewer.render_perspective(width, width * 3 // 4, "auto", max_points, 1.0, False)
        return _image_result(jpeg, {"cloud": info, **meta})

    @mcp.tool(annotations=VIEW)
    @tool
    def view_cloud(
        position: Annotated[list[float] | None, Field(description=(
            "Posição da câmera [x, y, z], coordenadas do projeto (m)."))] = None,
        look_at: Annotated[list[float] | None, Field(description=(
            "Ponto [x, y, z] para onde a câmera aponta (substitui yaw/pitch)."))] = None,
        yaw: Annotated[float | None, Field(description="Rumo em graus: 0 = norte (+Y), 90 = leste (+X).")] = None,
        pitch: Annotated[float | None, Field(description="Inclinação em graus: negativa olha para baixo.")] = None,
        fov: Annotated[float | None, Field(description="Campo de visão horizontal, 10 a 120 graus.")] = None,
        reset: Annotated[bool, Field(description=(
            "Volta para a vista geral antes de aplicar os outros parâmetros."))] = False,
        width: Width = 1024,
        height: Height = 768,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 2_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Mostra a nuvem em perspectiva a partir da câmera. Sem parâmetros, repete a vista atual; com
        parâmetros, posiciona a câmera de forma absoluta. A resposta traz a câmera e o ponto da nuvem no
        centro da imagem (center_point)."""
        with viewer.lock:
            viewer.require()
            if reset:
                viewer.overview()
            viewer.set_camera(position, look_at, yaw, pitch, fov)
            jpeg, meta = viewer.render_perspective(width, height, color_by, max_points, point_scale, pixel_grid)
        return _image_result(jpeg, meta)

    @mcp.tool(annotations=VIEW)
    @tool
    def move_camera(
        forward: Annotated[float, Field(description=(
            "Metros para a frente no rumo atual, na horizontal (negativo recua)."))] = 0.0,
        right: Annotated[float, Field(description="Metros para a direita (negativo: esquerda).")] = 0.0,
        up: Annotated[float, Field(description="Metros para cima (negativo: desce).")] = 0.0,
        turn_right: Annotated[float, Field(description=(
            "Graus de giro para a direita, aplicado depois de andar (negativo: esquerda)."))] = 0.0,
        turn_up: Annotated[float, Field(description=(
            "Graus para inclinar a vista para cima (negativo: para baixo)."))] = 0.0,
        width: Width = 1024,
        height: Height = 768,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 2_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Anda e gira a câmera em relação à posição atual, como caminhar dentro da nuvem, e devolve a
        nova imagem."""
        with viewer.lock:
            viewer.move(forward, right, up, turn_right, turn_up)
            jpeg, meta = viewer.render_perspective(width, height, color_by, max_points, point_scale, pixel_grid)
        return _image_result(jpeg, meta)

    @mcp.tool(annotations=VIEW)
    @tool
    def orbit_camera(
        right: Annotated[float, Field(description=(
            "Graus de giro da câmera em torno do pivô, no sentido horário visto de cima (negativo: anti-horário)."
        ))] = 0.0,
        up: Annotated[float, Field(description=(
            "Graus para subir a câmera em torno do pivô (negativo: desce)."))] = 0.0,
        zoom: Annotated[float, Field(gt=0.01, le=100, description=(
            "Fator de aproximação: 2 chega à metade da distância; 0.5 afasta para o dobro."))] = 1.0,
        pivot: Annotated[list[float] | None, Field(description=(
            "Pivô [x, y, z]. Padrão: o ponto no centro da última imagem."))] = None,
        width: Width = 1024,
        height: Height = 768,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 2_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Gira a câmera em torno de um ponto (órbita) e/ou aproxima, mantendo o ponto no centro."""
        with viewer.lock:
            pv = viewer.orbit(pivot, right, up, zoom)
            jpeg, meta = viewer.render_perspective(width, height, color_by, max_points, point_scale, pixel_grid)
            meta["pivot"] = viewer.require().to_world(pv)
        return _image_result(jpeg, meta)

    @mcp.tool(annotations=VIEW)
    @tool
    def pick_point(
        px: Annotated[int, Field(ge=0, description="Coluna do pixel na última imagem (0 = esquerda).")],
        py: Annotated[int, Field(ge=0, description="Linha do pixel na última imagem (0 = topo).")],
    ) -> dict[str, Any]:
        """Devolve as coordenadas [x, y, z] do ponto da nuvem desenhado num pixel da última imagem
        (perspectiva, planta ou corte). Dois pontos permitem calcular uma distância."""
        with viewer.lock:
            p = viewer.pick(px, py)
            s = viewer.require()
            out: dict[str, Any] = {"ok": True, "xyz": s.to_world(p), "pixel": [px, py],
                                   "from_view": viewer.last["kind"]}
            if viewer.last["kind"] == "perspective":
                out["distance_from_camera"] = round(float(np.linalg.norm(p - viewer.last["pos"])), 3)
        return out

    @mcp.tool(annotations=VIEW)
    @tool
    def go_to_pixel(
        px: Annotated[int, Field(ge=0, description="Coluna do pixel na última imagem.")],
        py: Annotated[int, Field(ge=0, description="Linha do pixel na última imagem.")],
        stop_distance: Annotated[float, Field(gt=0, le=1000, description=(
            "Distância em metros a manter do ponto alvo."))] = 3.0,
        eye_height: Annotated[float | None, Field(description=(
            "Se informado, a câmera fica a esta altura (m) acima do ponto alvo, olhando na horizontal no rumo "
            "atual: aponte para o piso e use 1.6 para ficar de pé ali."))] = None,
        width: Width = 1024,
        height: Height = 768,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 2_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Leva a câmera até o ponto da nuvem que aparece num pixel da última imagem (de qualquer vista)
        e devolve a nova imagem em perspectiva."""
        with viewer.lock:
            target = viewer.pick(px, py)
            s = viewer.require()
            if eye_height is not None:
                viewer.pos = target + np.array([0.0, 0.0, float(eye_height)])
                viewer.pitch = 0.0
            else:
                if viewer.last["kind"] != "perspective":
                    # vindo de planta ou corte: chega pelo sul, de cima, olhando para o alvo
                    viewer.pos = target + np.array([0.0, -stop_distance * 0.7, stop_distance * 0.7])
                else:
                    d = target - viewer.pos
                    dist = float(np.linalg.norm(d))
                    if dist > stop_distance:
                        viewer.pos = target - d / dist * stop_distance
                viewer.look_at(target)
            jpeg, meta = viewer.render_perspective(width, height, color_by, max_points, point_scale, pixel_grid)
            meta["target"] = s.to_world(target)
        return _image_result(jpeg, meta)

    @mcp.tool(annotations=VIEW)
    @tool
    def plan_view(
        center: Annotated[list[float] | None, Field(description=(
            "Centro [x, y] da planta. Padrão: a nuvem inteira."))] = None,
        width_m: Annotated[float | None, Field(gt=0, description="Largura da área (m, eixo X).")] = None,
        height_m: Annotated[float | None, Field(gt=0, description=(
            "Altura da área (m, eixo Y). Padrão: 3/4 da largura."))] = None,
        z_min: Annotated[float | None, Field(description="Cota inferior da faixa (m). Padrão: base da nuvem.")] = None,
        z_max: Annotated[float | None, Field(description=(
            "Cota superior da faixa (m): corta o que está acima, como tetos e coberturas."))] = None,
        width: Width = 1024,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 3_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Planta ortográfica (vista de cima, norte para cima) de uma faixa de cotas, com as coordenadas
        X e Y marcadas nas bordas. Para ver paredes e pilares, use uma faixa fina (por exemplo, de 1,0 a
        1,3 m acima do piso). Descubra as cotas dos pisos com section_view."""
        with viewer.lock:
            jpeg, meta = viewer.plan(center, width_m, height_m, z_min, z_max, width, color_by, max_points,
                                     point_scale, pixel_grid)
        return _image_result(jpeg, meta)

    @mcp.tool(annotations=VIEW)
    @tool
    def section_view(
        p1: Annotated[list[float], Field(description=(
            "Início da linha de corte [x, y] (fica à esquerda na imagem)."))],
        p2: Annotated[list[float], Field(description="Fim da linha de corte [x, y] (fica à direita).")],
        thickness: Annotated[float, Field(gt=0, le=200, description=(
            "Espessura da fatia em metros, centrada na linha."))] = 0.5,
        z_min: Annotated[float | None, Field(description="Cota inferior (m). Padrão: base da nuvem.")] = None,
        z_max: Annotated[float | None, Field(description="Cota superior (m). Padrão: topo da nuvem.")] = None,
        width: Width = 1024,
        color_by: ColorBy = "auto",
        max_points: MaxPoints = 3_000_000,
        point_scale: PointScale = 1.0,
        pixel_grid: PixelGrid = False,
    ) -> CallToolResult:
        """Corte vertical ortográfico ao longo da linha p1-p2, com a distância desde p1 e as cotas Z
        marcadas nas bordas. Serve para ler níveis de piso, pés-direitos, alturas e perfis."""
        with viewer.lock:
            jpeg, meta = viewer.section(p1, p2, thickness, z_min, z_max, width, color_by, max_points,
                                        point_scale, pixel_grid)
        return _image_result(jpeg, meta)

    return viewer
