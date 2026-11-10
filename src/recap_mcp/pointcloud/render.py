"""Renderização ortográfica de pontos em PNG (numpy + Pillow), com eixos, escala e legenda."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MAX_PX = 4096
BG = (250, 250, 250)
EMPTY = (235, 235, 235)
INK = (30, 30, 30)

# rampa de cores para elevação (azul → ciano → verde → amarelo → vermelho)
_RAMP = np.array([[49, 54, 149], [69, 117, 180], [116, 173, 209], [171, 217, 233], [254, 224, 144],
                  [253, 174, 97], [244, 109, 67], [215, 48, 39]], dtype=float)


def ramp(t: np.ndarray) -> np.ndarray:
    t = np.clip(np.nan_to_num(t), 0, 1) * (len(_RAMP) - 1)
    i0 = np.floor(t).astype(int)
    i1 = np.minimum(i0 + 1, len(_RAMP) - 1)
    f = (t - i0)[:, None]
    return (_RAMP[i0] * (1 - f) + _RAMP[i1] * f).astype(np.uint8)


def colorize(pts: dict[str, np.ndarray], color_by: str, has_rgb: bool, has_i: bool,
             value: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Cores RGB por ponto e a descrição da legenda."""
    n = len(pts["x"])
    if color_by == "rgb" and has_rgb:
        return np.column_stack([pts["r"], pts["g"], pts["b"]]).astype(np.uint8), {"color_by": "rgb"}
    if color_by == "intensity" and has_i and n:
        lo, hi = np.percentile(pts["i"], [2, 98])
        g = np.clip((pts["i"] - lo) / max(hi - lo, 1e-9), 0, 1)
        c = (g * 255).astype(np.uint8)
        return np.column_stack([c, c, c]), {"color_by": "intensity", "range_p2_p98": [float(lo), float(hi)]}
    v = pts["z"] if value is None else value
    if n == 0:
        return np.zeros((0, 3), np.uint8), {"color_by": "elevation"}
    lo, hi = float(np.percentile(v, 1)), float(np.percentile(v, 99))
    note = {"color_by": "elevation", "range_p1_p99": [lo, hi]}
    if color_by != "elevation":
        note["fallback"] = f"atributo '{color_by}' ausente; usada a elevação"
    return ramp((v - lo) / max(hi - lo, 1e-9)), note


def nice_step(span: float, target: int = 6) -> float:
    if span <= 0:
        return 1.0
    raw = span / target
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * p:
            return m * p
    return 10 * p


_FONT_CANDIDATES = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")


def _font(size: int):
    """Fonte com acentos (TrueType do sistema); a fonte embutida do Pillow não tem 'ç', 'ã' nem '—'."""
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fmt(v: float, step: float) -> str:
    dec = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    return f"{v:.{dec}f}"


@dataclass
class Axis:
    label: str
    sign: float = 1.0   # valor exibido = sign * coordenada da tela


def raster(u: np.ndarray, v: np.ndarray, depth: np.ndarray, colors: np.ndarray,
           extent: tuple[float, float, float, float], width_px: int, point_size: int,
           title: str, xaxis: Axis, yaxis: Axis, legend: dict | None, unit: str = "m") -> tuple[bytes, dict]:
    """Desenha os pontos (mais próximo vence) e devolve PNG + metadados."""
    umin, umax, vmin, vmax = extent
    # folga mínima: evita figuras degeneradas (ex.: perfil de área plana); a escala continua igual nos dois eixos
    du = max(umax - umin, 1e-6)
    if vmax - vmin < 0.15 * du:
        mid, half = (vmin + vmax) / 2, 0.075 * du
        vmin, vmax = mid - half, mid + half
    dv = max(vmax - vmin, 1e-6)
    if du < 0.15 * dv:
        mid, half = (umin + umax) / 2, 0.075 * dv
        umin, umax = mid - half, mid + half
        du = umax - umin
    w = int(min(max(width_px, 64), MAX_PX))
    h = int(min(max(round(w * dv / du), 32), MAX_PX))
    if round(w * dv / du) > MAX_PX:
        w = int(max(32, round(MAX_PX * du / dv)))
    px = du / w
    py = dv / h
    img = np.empty((h, w, 3), np.uint8)
    img[:] = EMPTY
    if len(u):
        col = np.clip(((u - umin) / px).astype(np.int64), 0, w - 1)
        row = np.clip(((vmax - v) / py).astype(np.int64), 0, h - 1)
        order = np.argsort(depth, kind="stable")          # menor profundidade = mais próximo
        flat = (row * w + col)[order]
        _, first = np.unique(flat, return_index=True)
        sel = order[first]
        r0, c0 = row[sel], col[sel]
        filled = np.zeros((h, w), bool)
        img[r0, c0] = colors[sel]
        filled[r0, c0] = True
        k = max(1, int(point_size))
        if k > 1:
            base = img.copy()
            for dr in range(-(k // 2), k - k // 2):
                for dc in range(-(k // 2), k - k // 2):
                    rr, cc = np.clip(r0 + dr, 0, h - 1), np.clip(c0 + dc, 0, w - 1)
                    empty = ~filled[rr, cc]
                    img[rr[empty], cc[empty]] = base[r0[empty], c0[empty]]
                    filled[rr[empty], cc[empty]] = True

    ml, mr, mt, mb = 78, 30 if not legend or legend.get("color_by") == "rgb" else 110, 34, 58
    canvas = Image.new("RGB", (w + ml + mr, h + mt + mb), BG)
    canvas.paste(Image.fromarray(img), (ml, mt))
    d = ImageDraw.Draw(canvas)
    f, fs = _font(13), _font(11)
    d.rectangle([ml - 1, mt - 1, ml + w, mt + h], outline=INK)
    d.text((ml, 8), title, fill=INK, font=f)

    # eixos com coordenadas
    for axis, lo, hi, horizontal in ((xaxis, umin, umax, True), (yaxis, vmin, vmax, False)):
        vals = sorted((axis.sign * lo, axis.sign * hi))
        step = nice_step(vals[1] - vals[0])
        t = math.ceil(vals[0] / step) * step
        while t <= vals[1] + 1e-9:
            screen = t / axis.sign
            if horizontal:
                x = ml + (screen - umin) / px
                d.line([x, mt + h, x, mt + h + 5], fill=INK)
                s = _fmt(t, step)
                d.text((x - d.textlength(s, font=fs) / 2, mt + h + 7), s, fill=INK, font=fs)
            else:
                y = mt + (vmax - screen) / py
                d.line([ml - 5, y, ml, y], fill=INK)
                s = _fmt(t, step)
                d.text((ml - 8 - d.textlength(s, font=fs), y - 6), s, fill=INK, font=fs)
            t += step
    d.text((ml + w / 2 - 30, mt + h + 24), f"{xaxis.label} ({unit})", fill=INK, font=f)
    d.text((6, mt - 18), f"{yaxis.label} ({unit})", fill=INK, font=f)

    # escala gráfica
    bar = nice_step(du, 5)
    blen = bar / px
    bx, by = ml + 12, mt + h - 16
    d.rectangle([bx - 4, by - 16, bx + blen + 60, by + 8], fill=(255, 255, 255))
    d.line([bx, by, bx + blen, by], fill=INK, width=3)
    d.line([bx, by - 5, bx, by + 5], fill=INK)
    d.line([bx + blen, by - 5, bx + blen, by + 5], fill=INK)
    d.text((bx + blen + 6, by - 8), f"{_fmt(bar, bar)} {unit}", fill=INK, font=fs)

    # legenda
    if legend and legend.get("color_by") in ("elevation", "intensity"):
        lx, ly, lh = ml + w + 14, mt + 10, min(220, h - 20)
        rng = legend.get("range_p1_p99") or legend.get("range_p2_p98")
        for j in range(lh):
            tval = 1 - j / max(lh - 1, 1)
            c = tuple(int(x) for x in (ramp(np.array([tval]))[0] if legend["color_by"] == "elevation"
                                      else [int(255 * tval)] * 3))
            d.line([lx, ly + j, lx + 16, ly + j], fill=c)
        if rng:
            d.text((lx + 20, ly - 4), f"{rng[1]:.3f}", fill=INK, font=fs)
            d.text((lx + 20, ly + lh - 8), f"{rng[0]:.3f}", fill=INK, font=fs)
        d.text((lx - 2, ly + lh + 6), "elevação" if legend["color_by"] == "elevation" else "intensidade",
               fill=INK, font=fs)

    bio = io.BytesIO()
    canvas.save(bio, format="PNG", optimize=True)
    return bio.getvalue(), {"width_px": canvas.width, "height_px": canvas.height, "pixel_size": [px, py],
                            "extent": {"u": [umin, umax], "v": [vmin, vmax]}}
