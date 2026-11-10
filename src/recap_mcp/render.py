"""Renderização de pontos em imagem (numpy + Pillow): perspectiva e vistas ortográficas.

Sem GPU e sem programas externos: projeção, z-buffer por ordenação, pontos com tamanho
proporcional ao espaçamento da amostra e sombreamento por profundidade (eye-dome lighting),
que dá leitura de relevo mesmo em nuvens sem cor.
"""

from __future__ import annotations

import io
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BACKGROUND = np.array([22, 24, 28], np.uint8)
MAX_SPLAT = 5

_RAMP = np.array([[49, 54, 149], [69, 117, 180], [116, 173, 209], [171, 217, 233], [254, 224, 144],
                  [253, 174, 97], [244, 109, 67], [215, 48, 39]], np.float32)


def ramp(t: np.ndarray) -> np.ndarray:
    t = np.clip(np.nan_to_num(t), 0, 1) * (len(_RAMP) - 1)
    i0 = np.floor(t).astype(int)
    i1 = np.minimum(i0 + 1, len(_RAMP) - 1)
    f = (t - i0)[:, None]
    return (_RAMP[i0] * (1 - f) + _RAMP[i1] * f).astype(np.uint8)


def colorize(pts: dict[str, np.ndarray], color_by: str, z_world: np.ndarray) -> tuple[np.ndarray, str]:
    """Cor por ponto. ``auto`` usa RGB se a nuvem tiver cor; senão, intensidade."""
    rgb, inten = pts["rgb"], pts["i"]
    if color_by == "auto":
        color_by = "rgb" if len(rgb) and int(rgb[: 200_000].max()) > 0 else "intensity"
    if color_by == "rgb":
        return rgb, "rgb"
    if color_by == "intensity":
        if len(inten) == 0:
            return rgb, "intensity"
        lo, hi = np.percentile(inten[:: max(1, len(inten) // 200_000)], [2, 98])
        g = (np.clip((inten.astype(np.float32) - lo) / max(hi - lo, 1e-6), 0, 1) * 235 + 20).astype(np.uint8)
        return np.stack([g, g, g], 1), "intensity"
    if color_by == "elevation":
        if len(z_world) == 0:
            return rgb, "elevation"
        lo, hi = np.percentile(z_world[:: max(1, len(z_world) // 200_000)], [1, 99])
        return ramp((z_world - lo) / max(hi - lo, 1e-6)), f"elevation {lo:.2f} a {hi:.2f} m (azul a vermelho)"
    raise ValueError(color_by)


def rasterize(u: np.ndarray, v: np.ndarray, depth: np.ndarray, size: np.ndarray, rgb: np.ndarray,
              width: int, height: int, edl: float = 1.0, fill: int = 0,
              gain: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Desenha os pontos (pixel ``u, v``; profundidade crescente para longe). Devolve imagem e profundidade."""
    zbuf = np.full(width * height, np.inf, np.float32)
    src = np.full(width * height, -1, np.int64)
    if len(u):
        order = np.argsort(-depth, kind="stable")       # do mais distante para o mais próximo
        ui = np.floor(u[order]).astype(np.int32)
        vi = np.floor(v[order]).astype(np.int32)
        d = depth[order].astype(np.float32)
        s = np.clip(size[order], 1, MAX_SPLAT).astype(np.int32)
        lo, hi = -(s // 2), (s - 1) // 2
        zpass = np.empty_like(zbuf)
        spass = np.empty_like(src)
        for oy in range(-(MAX_SPLAT // 2), (MAX_SPLAT - 1) // 2 + 1):
            for ox in range(-(MAX_SPLAT // 2), (MAX_SPLAT - 1) // 2 + 1):
                sel = (lo <= min(ox, oy)) & (hi >= max(ox, oy))
                if ox or oy:
                    if not sel.any():
                        continue
                    x, y, dd, ids = ui[sel] + ox, vi[sel] + oy, d[sel], order[sel]
                else:
                    x, y, dd, ids = ui, vi, d, order
                ok = (x >= 0) & (x < width) & (y >= 0) & (y < height)
                idx = y[ok].astype(np.int64) * width + x[ok]
                zpass.fill(np.inf)
                zpass[idx] = dd[ok]                      # índice repetido: vale o último, o mais próximo
                spass[idx] = ids[ok]
                closer = zpass < zbuf
                zbuf[closer] = zpass[closer]
                src[closer] = spass[closer]
    zimg = zbuf.reshape(height, width)
    simg = src.reshape(height, width)
    for _ in range(fill):
        zimg, simg = _fill_holes(zimg, simg)
    hit = simg >= 0
    img = np.empty((height, width, 3), np.uint8)
    img[:] = BACKGROUND
    img[hit] = rgb[simg[hit]]
    if gain and hit.any():
        lum = img[hit].max(1)
        g = float(np.clip(215.0 / max(float(np.percentile(lum, 98)), 1.0), 1.0, 4.0))
        if g > 1.02:
            img[hit] = np.clip(img[hit].astype(np.float32) * g, 0, 255).astype(np.uint8)
    if edl > 0 and hit.any():
        img = _eye_dome(img, zimg, edl)
    return img, zimg


def _fill_holes(z: np.ndarray, src: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fecha furos pequenos com o vizinho mais próximo da câmera.

    Furo é o pixel vazio com 4 ou mais vizinhos desenhados, ou o pixel que mostra algo bem mais
    distante (mais de 8%) que 5 ou mais dos seus 8 vizinhos: um ponto de trás aparecendo por uma
    falha da superfície da frente.
    """
    best_z = np.full_like(z, np.inf)
    best_s = np.full_like(src, -1)
    n = np.zeros(z.shape, np.int8)
    nearer = np.zeros(z.shape, np.int8)
    limit = np.where(np.isfinite(z), z * 0.92, -np.inf)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            nz, ns = np.roll(z, (dy, dx), (0, 1)), np.roll(src, (dy, dx), (0, 1))
            valid = np.isfinite(nz)
            n += valid
            nearer += valid & (nz < limit)
            closer = valid & (nz < best_z)
            best_z[closer] = nz[closer]
            best_s[closer] = ns[closer]
    hole = ((~np.isfinite(z)) & (n >= 4)) | (nearer >= 5)
    z, src = z.copy(), src.copy()
    z[hole] = best_z[hole]
    src[hole] = best_s[hole]
    return z, src


def _eye_dome(img: np.ndarray, z: np.ndarray, strength: float) -> np.ndarray:
    """Escurece o pixel que fica atrás dos vizinhos: realça arestas e degraus de profundidade."""
    hit = np.isfinite(z)
    zz = np.where(hit, z, np.nan)
    span = float(np.nanmax(zz) - np.nanmin(zz))
    if z.min() > 0 and np.nanmax(zz) / max(np.nanmin(zz), 1e-6) > 1.05:
        lz = np.log2(np.where(hit, np.maximum(z, 1e-6), 1.0)).astype(np.float32)       # perspectiva
    else:
        lz = (np.where(hit, z, 0.0) / max(span, 1e-6) * 4.0).astype(np.float32)        # ortográfica
    resp = np.zeros_like(lz)
    for r in (1, 2):
        for dy, dx in ((r, 0), (-r, 0), (0, r), (0, -r)):
            nb = np.roll(lz, (dy, dx), (0, 1))
            nb_hit = np.roll(hit, (dy, dx), (0, 1))
            resp += np.where(nb_hit, np.maximum(0.0, lz - nb - 0.04), 0.0)   # ignora degraus pequenos
    shade = np.exp(-resp / 8.0 * 40.0 * strength)
    shade = np.where(hit, np.clip(shade, 0.35, 1.0), 1.0)
    return (img.astype(np.float32) * shade[..., None]).astype(np.uint8)


# --------------------------------------------------------------------- textos

_FONTS: dict[int, ImageFont.ImageFont] = {}


def font(size: int = 13) -> ImageFont.ImageFont:
    if size not in _FONTS:
        # caminho completo: clientes MCP podem iniciar o servidor sem a variável WINDIR,
        # e sem ela o Pillow não acha as fontes do Windows pelo nome
        win = os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                _FONTS[size] = ImageFont.truetype(os.path.join(win, "Fonts", name), size)
                break
            except OSError:
                try:
                    _FONTS[size] = ImageFont.truetype(name, size)
                    break
                except OSError:
                    continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def label(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, anchor: str = "la", size: int = 13) -> None:
    f = font(size)
    box = draw.textbbox(xy, text, font=f, anchor=anchor)
    draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(0, 0, 0))
    draw.text(xy, text, font=f, fill=(235, 235, 235), anchor=anchor)


def nice_step(span: float, target: int = 7) -> float:
    if span <= 0:
        return 1.0
    raw = span / target
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * p:
            return m * p
    return 10 * p


def fmt(v: float, step: float) -> str:
    dec = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    return f"{v:.{dec}f}"


def draw_axes(img: Image.Image, h_range: tuple[float, float], v_range: tuple[float, float],
              h_name: str, v_name: str) -> None:
    """Marcas de coordenadas nas bordas de uma vista ortográfica (horizontal embaixo, vertical à esquerda)."""
    d = ImageDraw.Draw(img)
    w, h = img.size
    h0, h1 = h_range
    v0, v1 = v_range                      # v0 embaixo, v1 em cima
    step = nice_step(h1 - h0, max(3, w // 130))
    t = math.ceil(h0 / step) * step
    while t <= h1:
        x = (t - h0) / (h1 - h0) * w
        d.line((x, h - 9, x, h), fill=(255, 220, 90), width=2)
        if 30 < x < w - 30:
            label(d, (x, h - 11), fmt(t, step), "mb", 12)
        t += step
    step_v = nice_step(v1 - v0, max(3, h // 90))
    t = math.ceil(v0 / step_v) * step_v
    while t <= v1:
        y = h - (t - v0) / (v1 - v0) * h
        d.line((0, y, 9, y), fill=(255, 220, 90), width=2)
        if 14 < y < h - 30:
            label(d, (12, y), fmt(t, step_v), "lm", 12)
        t += step_v
    label(d, (w - 6, h - 30), f"{h_name} →   marcas a cada {fmt(step, step)} m", "rb", 12)
    label(d, (6, 6), f"↑ {v_name}   marcas a cada {fmt(step_v, step_v)} m", "la", 12)


def to_jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
