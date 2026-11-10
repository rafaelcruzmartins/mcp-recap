"""Visualizador: a nuvem aberta, a câmera e a última imagem gerada (para apontar pixels).

Convenções: coordenadas do projeto em metros, Z para cima. ``yaw`` é o rumo em graus,
0 = +Y (norte) e 90 = +X (leste); ``pitch`` é a inclinação, positiva para cima.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import errors, render
from .scene import Scene

NEAR = 0.1
COLOR_MODES = ("auto", "rgb", "intensity", "elevation")
_COMPASS = ("N", "NE", "L", "SE", "S", "SO", "O", "NO")


def _basis(yaw: float, pitch: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y, p = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p)])
    right = np.array([math.cos(y), -math.sin(y), 0.0])
    return fwd, right, np.cross(right, fwd)


def _vec3(value: Any, name: str) -> np.ndarray:
    try:
        v = np.array([float(c) for c in value], np.float64)
    except (TypeError, ValueError):
        v = np.zeros(0)
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{name}' deve ser [x, y, z] em metros.")
    return v


def _vec2(value: Any, name: str) -> np.ndarray:
    try:
        v = np.array([float(c) for c in value], np.float64)[:2]
    except (TypeError, ValueError):
        v = np.zeros(0)
    if v.shape != (2,) or not np.all(np.isfinite(v)):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{name}' deve ser [x, y] em metros.")
    return v


class Viewer:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scene: Scene | None = None
        self.pos = np.zeros(3)      # coordenadas da cena
        self.yaw = 0.0
        self.pitch = 0.0
        self.fov = 75.0
        self.last: dict[str, Any] | None = None

    # ----------------------------------------------------------------- estado

    def require(self) -> Scene:
        if self.scene is None:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT,
                                       "Nenhuma nuvem aberta. Chame open_cloud com um .rcp ou .rcs.")
        return self.scene

    def open(self, path: Path) -> dict[str, Any]:
        scene = Scene(path)
        if self.scene is not None:
            self.scene.close()
        self.scene, self.last, self.fov = scene, None, 75.0
        self.overview()
        return scene.info()

    def overview(self) -> None:
        """Câmera a sudoeste e acima, olhando para o centro da nuvem."""
        s = self.require()
        c = (s.bounds_min + s.bounds_max) / 2
        size = s.bounds_max - s.bounds_min
        self.yaw, self.pitch = 45.0, -35.0
        fwd, _, _ = _basis(self.yaw, self.pitch)
        dist = 0.40 * float(np.linalg.norm(size[:2])) / math.tan(math.radians(self.fov) / 2) + size[2] / 2
        self.pos = c - fwd * dist

    def look_at(self, target: np.ndarray) -> None:
        d = target - self.pos
        n = float(np.linalg.norm(d))
        if n < 1e-6:
            return
        self.yaw = math.degrees(math.atan2(d[0], d[1])) % 360
        self.pitch = math.degrees(math.asin(max(-1.0, min(1.0, d[2] / n))))

    def camera(self) -> dict[str, Any]:
        s = self.require()
        return {"position": s.to_world(self.pos), "yaw": round(self.yaw % 360, 2),
                "heading": _COMPASS[int(((self.yaw % 360) + 22.5) // 45) % 8],
                "pitch": round(self.pitch, 2), "fov": round(self.fov, 1)}

    def set_camera(self, position: Any = None, look_at: Any = None, yaw: float | None = None,
                   pitch: float | None = None, fov: float | None = None) -> None:
        s = self.require()
        if position is not None:
            self.pos = s.to_scene(_vec3(position, "position"))
        if yaw is not None:
            self.yaw = float(yaw) % 360
        if pitch is not None:
            self.pitch = max(-89.9, min(89.9, float(pitch)))
        if fov is not None:
            self.fov = max(10.0, min(120.0, float(fov)))
        if look_at is not None:
            self.look_at(s.to_scene(_vec3(look_at, "look_at")))

    def move(self, forward: float, right: float, up: float, turn_right: float, turn_up: float) -> None:
        self.require()
        heading, side, _ = _basis(self.yaw, 0.0)      # andar: no plano horizontal, no rumo atual
        self.pos = self.pos + heading * forward + side * right + np.array([0, 0, up])
        self.yaw = (self.yaw + turn_right) % 360
        self.pitch = max(-89.9, min(89.9, self.pitch + turn_up))

    def orbit(self, pivot: Any, right_deg: float, up_deg: float, zoom: float) -> np.ndarray:
        s = self.require()
        if pivot is not None:
            pv = s.to_scene(_vec3(pivot, "pivot"))
        elif self.last and self.last.get("center_scene") is not None:
            pv = self.last["center_scene"]
        else:
            pv = (s.bounds_min + s.bounds_max) / 2
        d = self.pos - pv
        dist = max(float(np.linalg.norm(d)), 0.5)
        az = math.atan2(d[0], d[1]) + math.radians(right_deg)
        el = math.asin(max(-1, min(1, d[2] / dist))) + math.radians(up_deg)
        el = max(math.radians(-89), min(math.radians(89), el))
        dist = dist / max(zoom, 1e-3)
        self.pos = pv + dist * np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])
        self.look_at(pv)
        return pv

    # ------------------------------------------------------------ perspectiva

    def render_perspective(self, width: int, height: int, color_by: str, max_points: int,
                           point_scale: float, pixel_grid: bool) -> tuple[bytes, dict[str, Any]]:
        s = self.require()
        t0 = time.perf_counter()
        fwd, right, up = _basis(self.yaw, self.pitch)
        f = (width / 2) / math.tan(math.radians(self.fov) / 2)
        rel = s.leaf_center.astype(np.float64) - self.pos
        dist = np.linalg.norm(rel, axis=1)
        depth = rel @ fwd
        r = s.leaf_radius.astype(np.float64)
        half_diag = math.atan(math.hypot(width / 2, height / 2) / f)
        ang = np.arccos(np.clip(depth / np.maximum(dist, 1e-9), -1, 1))
        vis = ((depth + r > NEAR) & (ang <= half_diag + np.arcsin(np.clip(r / np.maximum(dist, 1e-9), 0, 1)))) \
            | (dist < r)
        idx = np.nonzero(vis)[0]
        ext = (s.leaf_max - s.leaf_min).max(1)
        px = f * ext[idx] / np.maximum(dist[idx] - r[idx], 0.25 * r[idx] + 1e-3)
        want = np.minimum(px * px, width * height) * 1.2
        take = s.allocate(s.leaf_count[idx], want, max_points, key=idx)
        pts = s.gather(idx, take)
        t1 = time.perf_counter()

        xyz = pts["xyz"].astype(np.float32)
        d = xyz - self.pos.astype(np.float32)
        zc = d @ fwd.astype(np.float32)
        ok = zc > NEAR
        xc, yc = d @ right.astype(np.float32), d @ up.astype(np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            u = width / 2 + f * xc / zc
            v = height / 2 - f * yc / zc
        ok &= (u > -8) & (u < width + 8) & (v > -8) & (v < height + 8)
        # tamanho do ponto: espaçamento da amostra da folha, projetado
        spacing = np.zeros(len(s.leaf_count), np.float32)
        spacing[pts["leaves"]] = ext[pts["leaves"]] / np.sqrt(np.maximum(pts["taken"], 1))
        with np.errstate(divide="ignore", invalid="ignore"):
            size = np.ceil(f * spacing[pts["leaf"]] / np.maximum(zc, NEAR) * 1.2 * point_scale)
        colors, mode = render.colorize(pts, color_by, xyz[:, 2] + np.float32(s.origin[2]))
        img, zimg = render.rasterize(u[ok], v[ok], zc[ok], size[ok], colors[ok], width, height, edl=0.5, fill=3)
        t2 = time.perf_counter()

        self.last = {"kind": "perspective", "pos": self.pos.copy(), "basis": (fwd, right, up), "f": f,
                     "width": width, "height": height, "z": zimg, "center_scene": None}
        center = self._unproject(width // 2, height // 2, radius=10)
        self.last["center_scene"] = center
        pil = Image.fromarray(img)
        dr = ImageDraw.Draw(pil)
        cx, cy = width // 2, height // 2
        dr.line((cx - 7, cy, cx + 7, cy), fill=(255, 220, 90), width=1)
        dr.line((cx, cy - 7, cx, cy + 7), fill=(255, 220, 90), width=1)
        cam = self.camera()
        p = cam["position"]
        render.label(dr, (6, height - 6),
                     f"câmera X={p[0]:.2f} Y={p[1]:.2f} Z={p[2]:.2f} | rumo {cam['yaw']:.0f}° ({cam['heading']}) | "
                     f"inclinação {cam['pitch']:.0f}° | campo {cam['fov']:.0f}°", "lb", 12)
        if pixel_grid:
            _pixel_grid(dr, width, height)
        meta = {
            "view": "perspective", "camera": cam, "image_size": [width, height], "color": mode,
            "center_point": None if center is None else {
                "xyz": s.to_world(center), "distance": round(float(np.linalg.norm(center - self.pos)), 3)},
            "points_drawn": int(ok.sum()), "points_read": len(xyz), "leaves_visible": len(idx),
            "empty": bool(not np.isfinite(zimg).any()),
            "ms": {"read": round((t1 - t0) * 1000), "draw": round((t2 - t1) * 1000)},
        }
        if meta["empty"]:
            meta["hint"] = ("Nenhum ponto no campo de visão. Use view_cloud com look_at num ponto dentro de "
                            "'bounds' (open_cloud), ou reset=true.")
        return render.to_jpeg(pil), meta

    # ----------------------------------------------------------- ortográficas

    def render_ortho(self, kind: str, center: np.ndarray, e_h: np.ndarray, e_v: np.ndarray, e_d: np.ndarray,
                     half_w: float, half_h: float, d0: float, d1: float, width: int, color_by: str,
                     max_points: int, point_scale: float, pixel_grid: bool,
                     h_axis: tuple[str, float], v_axis: tuple[str, float], extra: dict[str, Any]
                     ) -> tuple[bytes, dict[str, Any]]:
        """Vista ortográfica de uma caixa. ``center`` na cena; ``e_d`` aponta para longe do observador."""
        s = self.require()
        t0 = time.perf_counter()
        ppm = width / (2 * half_w)
        height = round(2 * half_h * ppm)
        if not 64 <= height <= 2048:
            raise errors.RecapMcpError(
                errors.INVALID_ARGUMENT,
                f"Proporção da vista fora do limite (imagem de {width} x {height} px). Ajuste a largura, a "
                "altura ou a faixa de cotas para ficar entre 1:16 e 2:1.")
        rel = s.leaf_center.astype(np.float64) - center
        r = s.leaf_radius.astype(np.float64)
        h, v, d = rel @ e_h, rel @ e_v, rel @ e_d
        vis = (np.abs(h) <= half_w + r) & (np.abs(v) <= half_h + r) & (d + r >= d0) & (d - r <= d1)
        idx = np.nonzero(vis)[0]
        overlap = (np.minimum(d1, d[idx] + r[idx]) - np.maximum(d0, d[idx] - r[idx])) / (2 * r[idx] + 1e-9)
        px = 2 * r[idx] * ppm
        want = np.minimum(px * px, width * height) * 1.5 / np.clip(overlap, 0.02, 1.0)
        take = s.allocate(s.leaf_count[idx], want, max_points, max_scale=64.0, key=idx)   # cortes finos pedem densidade
        pts = s.gather(idx, take)
        t1 = time.perf_counter()

        xyz = pts["xyz"]
        dd = xyz - center.astype(np.float32)
        ph, pv, pd = dd @ e_h.astype(np.float32), dd @ e_v.astype(np.float32), dd @ e_d.astype(np.float32)
        ok = (np.abs(ph) <= half_w) & (np.abs(pv) <= half_h) & (pd >= d0) & (pd <= d1)
        u = (ph + half_w) * ppm
        vv = (half_h - pv) * ppm
        spacing = np.zeros(len(s.leaf_count), np.float32)
        ext = (s.leaf_max - s.leaf_min).max(1)
        spacing[pts["leaves"]] = ext[pts["leaves"]] / np.sqrt(np.maximum(pts["taken"], 1))
        size = np.clip(np.ceil(spacing[pts["leaf"]] * ppm * 0.7 * point_scale), 1, 3)
        colors, mode = render.colorize(pts, color_by, xyz[:, 2] + np.float32(s.origin[2]))
        img, zimg = render.rasterize(u[ok], vv[ok], pd[ok] - np.float32(d0), size[ok], colors[ok], width, height,
                                     edl=0.5 if kind == "plan" else 0.0)
        t2 = time.perf_counter()
        self.last = {"kind": "ortho", "center": center.copy(), "axes": (e_h, e_v, e_d), "half": (half_w, half_h),
                     "ppm": ppm, "d0": d0, "width": width, "height": height, "z": zimg, "center_scene": None}
        self.last["center_scene"] = self._unproject(width // 2, height // 2, radius=10)
        pil = Image.fromarray(img)
        render.draw_axes(pil, (h_axis[1] - half_w, h_axis[1] + half_w), (v_axis[1] - half_h, v_axis[1] + half_h),
                         h_axis[0], v_axis[0])
        if pixel_grid:
            _pixel_grid(ImageDraw.Draw(pil), width, height)
        meta = {"view": kind, **extra, "image_size": [width, height],
                "meters_per_pixel": round(1 / ppm, 5), "color": mode,
                "points_drawn": int(ok.sum()), "points_read": len(xyz), "leaves_visible": len(idx),
                "empty": bool(not np.isfinite(zimg).any()),
                "ms": {"read": round((t1 - t0) * 1000), "draw": round((t2 - t1) * 1000)}}
        if meta["empty"]:
            meta["hint"] = "Nenhum ponto nesta caixa. Confira as coordenadas e a faixa de cotas com 'bounds'."
        return render.to_jpeg(pil), meta

    def plan(self, center: Any, width_m: float, height_m: float | None, z_min: float | None, z_max: float | None,
             width: int, color_by: str, max_points: int, point_scale: float, pixel_grid: bool
             ) -> tuple[bytes, dict[str, Any]]:
        s = self.require()
        lo, hi = s.bounds_min + s.origin, s.bounds_max + s.origin
        if center is None:
            c2 = (lo[:2] + hi[:2]) / 2
            if width_m is None:
                width_m = float((hi - lo)[0]) * 1.04
                height_m = float((hi - lo)[1]) * 1.04
        else:
            c2 = _vec2(center, "center")
        if width_m is None:
            width_m = 40.0
        height_m = float(height_m) if height_m else width_m * 0.75
        z0 = float(lo[2]) - 0.01 if z_min is None else float(z_min)
        z1 = float(hi[2]) + 0.01 if z_max is None else float(z_max)
        if not z1 > z0 or width_m <= 0 or height_m <= 0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Dimensões inválidas: exige z_max > z_min e "
                                                                "largura/altura positivas.")
        center3 = np.array([c2[0], c2[1], z1]) - s.origin          # observador no topo da faixa, olhando para baixo
        return self.render_ortho(
            "plan", center3, np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, -1.0]),
            width_m / 2, height_m / 2, 0.0, z1 - z0, width, color_by, max_points, point_scale, pixel_grid,
            ("X (leste)", float(c2[0])), ("Y (norte)", float(c2[1])),
            {"center": [round(float(c2[0]), 3), round(float(c2[1]), 3)], "width_m": round(width_m, 3),
             "height_m": round(height_m, 3), "z_range": [round(z0, 3), round(z1, 3)],
             "note": "Vista de cima; em cada pixel aparece o ponto mais alto dentro da faixa de cotas."})

    def section(self, p1: Any, p2: Any, thickness: float, z_min: float | None, z_max: float | None,
                width: int, color_by: str, max_points: int, point_scale: float, pixel_grid: bool
                ) -> tuple[bytes, dict[str, Any]]:
        s = self.require()
        a, b = _vec2(p1, "p1"), _vec2(p2, "p2")
        length = float(np.linalg.norm(b - a))
        if length < 0.05 or thickness <= 0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "p1 e p2 precisam ser distintos e a espessura, "
                                                                "positiva.")
        lo, hi = s.bounds_min + s.origin, s.bounds_max + s.origin
        z0 = float(lo[2]) - 0.2 if z_min is None else float(z_min)
        z1 = float(hi[2]) + 0.2 if z_max is None else float(z_max)
        if not z1 > z0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Exige z_max > z_min.")
        e_h = np.array([(b - a)[0] / length, (b - a)[1] / length, 0.0])
        e_v = np.array([0, 0, 1.0])
        e_d = np.cross(e_v, e_h)                 # observador olha com p1 à esquerda e p2 à direita
        mid = (a + b) / 2
        center3 = np.array([mid[0], mid[1], (z0 + z1) / 2]) - s.origin
        look = math.degrees(math.atan2(e_d[0], e_d[1])) % 360
        return self.render_ortho(
            "section", center3, e_h, e_v, e_d, length / 2, (z1 - z0) / 2, -thickness / 2, thickness / 2,
            width, color_by, max_points, point_scale, pixel_grid,
            ("distância desde p1", length / 2), ("Z (cota)", (z0 + z1) / 2),
            {"p1": [round(float(a[0]), 3), round(float(a[1]), 3)], "p2": [round(float(b[0]), 3), round(float(b[1]), 3)],
             "length_m": round(length, 3), "thickness_m": thickness, "z_range": [round(z0, 3), round(z1, 3)],
             "looking_toward_yaw": round(look, 1),
             "note": "Corte vertical: p1 à esquerda, p2 à direita; eixo horizontal em metros a partir de p1."})

    # ----------------------------------------------------------------- apontar

    def _unproject(self, px: int, py: int, radius: int = 6) -> np.ndarray | None:
        last = self.last
        if last is None:
            return None
        z = last["z"]
        h, w = z.shape
        if not (0 <= px < w and 0 <= py < h):
            return None
        x0, x1, y0, y1 = max(0, px - radius), min(w, px + radius + 1), max(0, py - radius), min(h, py + radius + 1)
        win = z[y0:y1, x0:x1]
        if not np.isfinite(win).any():
            return None
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = np.where(np.isfinite(win), (yy - py) ** 2 + (xx - px) ** 2, np.inf)
        j = int(np.argmin(d2))
        qy, qx = int(yy.flat[j]), int(xx.flat[j])
        depth = float(z[qy, qx])
        if last["kind"] == "perspective":
            fwd, right, up = last["basis"]
            f = last["f"]
            xc = (qx + 0.5 - w / 2) / f * depth
            yc = -(qy + 0.5 - h / 2) / f * depth
            return last["pos"] + right * xc + up * yc + fwd * depth
        e_h, e_v, e_d = last["axes"]
        half_w, half_h = last["half"]
        return (last["center"] + e_h * ((qx + 0.5) / last["ppm"] - half_w)
                + e_v * (half_h - (qy + 0.5) / last["ppm"]) + e_d * (depth + last["d0"]))

    def pick(self, px: int, py: int) -> np.ndarray:
        self.require()
        if self.last is None:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Ainda não há imagem. Gere uma vista primeiro.")
        w, h = self.last["width"], self.last["height"]
        if not (0 <= px < w and 0 <= py < h):
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT,
                                       f"Pixel fora da última imagem ({w} x {h} px).", px=px, py=py)
        p = self._unproject(px, py)
        if p is None:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT,
                                       "Não há ponto da nuvem nesse pixel nem a até 6 px dele (fundo vazio).",
                                       px=px, py=py)
        return p


def _pixel_grid(dr: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Grade de referência com as coordenadas de pixel, para apontar com pick_point / go_to_pixel."""
    step = 128 if width > 700 else 64
    for x in range(step, width, step):
        dr.line((x, 0, x, height), fill=(90, 200, 255), width=1)
        render.label(dr, (x + 2, 22), str(x), "la", 11)
    for y in range(step, height, step):
        dr.line((0, y, width, y), fill=(90, 200, 255), width=1)
        render.label(dr, (width - 4, y + 2), str(y), "ra", 11)
