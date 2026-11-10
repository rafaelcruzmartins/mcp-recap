"""Consultas sobre o índice: resumos, recortes e imagens. Nunca devolve a nuvem inteira."""

from __future__ import annotations

import math
import os
from typing import Any

import numpy as np

from .. import errors
from .index import EDGE_TOL, MAX_QUERY_POINTS, CloudIndex, Region
from .render import Axis, colorize, raster

DEFAULT_SAMPLE = 2000
MAX_SAMPLE = 10000
MAX_BINS = 5000
RENDER_MAX_POINTS = int(os.environ.get("RECAP_MCP_RENDER_MAX_POINTS", "4000000"))


# ---------------------------------------------------------------------------- utilidades

def _vec(v: Any, n: int, label: str) -> np.ndarray:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' deve ser uma lista com {n} números.")
    try:
        a = np.asarray([float(x) for x in v], dtype=float)
    except (TypeError, ValueError) as exc:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' contém valor não numérico.") from exc
    if not np.all(np.isfinite(a)):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' contém valor não finito.")
    return a


def _positive(v: float, label: str, maxv: float = 1e6) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0 < float(v) <= maxv):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"'{label}' deve ser > 0 e ≤ {maxv:g}.")
    return float(v)


def _sample_size(n: Any) -> int:
    if not isinstance(n, int) or isinstance(n, bool) or not (0 <= n <= MAX_SAMPLE):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"sample_size deve estar entre 0 e {MAX_SAMPLE}.")
    return n


def box_region(cloud: CloudIndex, bmin: Any = None, bmax: Any = None) -> Region:
    cmn, cmx = cloud.bounds
    mn = cmn.copy() if bmin is None else _vec(bmin, 3, "min")
    mx = cmx.copy() if bmax is None else _vec(bmax, 3, "max")
    if np.any(mx < mn):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Cada coordenada de 'max' deve ser ≥ a de 'min'.")
    return Region(mn, mx)


def _pick(n: int, k: int) -> np.ndarray:
    if n <= k:
        return np.arange(n)
    return np.sort(np.random.default_rng(0).choice(n, size=k, replace=False))


def _rows(pts: dict[str, np.ndarray], idx: np.ndarray, cloud: CloudIndex, cols: list[str]) -> dict[str, Any]:
    out_cols = list(cols)
    data = [np.round(pts[c][idx], 4) for c in cols]
    if cloud.m["has_intensity"]:
        out_cols.append("i")
        data.append(np.round(pts["i"][idx].astype(float), 2))
    if cloud.m["has_rgb"]:
        out_cols += ["r", "g", "b"]
        data += [pts[c][idx].astype(int) for c in ("r", "g", "b")]
    rows = np.column_stack(data).tolist() if len(idx) else []
    return {"columns": out_cols, "rows": rows}


def frame_info(cloud: CloudIndex) -> dict[str, Any]:
    """Declara explicitamente o sistema das coordenadas devolvidas."""
    srcs = cloud.m["sources"]
    frames = sorted({s["coordinate_frame"] for s in srcs})
    units = sorted({s["unit"] for s in srcs})
    geo = [s["georeferenced"] for s in srcs]
    info: dict[str, Any] = {
        "coordinates": "arquivo de origem",
        "frames": frames,
        "unit": units[0] if len(units) == 1 else units,
        "georeferenced": True if all(g is True for g in geo) else (False if all(g is False for g in geo) else None),
        "scan_local": False,
    }
    ctx = cloud.m.get("context") or {}
    pb = ctx.get("project_bounds")
    if ctx.get("via_project") and pb:
        mn, mx = cloud.bounds
        ext = float(np.max(mx - mn)) or 1.0
        tol = max(0.01, 1e-4 * ext)
        same = bool(np.all(np.abs(np.asarray(pb["min"]) - mn) <= tol) and np.all(np.abs(np.asarray(pb["max"]) - mx) <= tol))
        info["matches_recap_project_bounds"] = same
        info["recap_project_coordinate_system"] = ctx.get("project_coordinate_system")
        info["note"] = ("Os limites lidos coincidem com os do projeto ReCap; as coordenadas equivalem às do projeto."
                        if same else
                        "Os limites lidos NÃO coincidem com os do projeto ReCap (scan registrado ou movido no ReCap); "
                        "as coordenadas são as do arquivo de origem, não as do projeto.")
    return info


def _base(cloud: CloudIndex, res: dict[str, Any]) -> dict[str, Any]:
    out = {"coordinate_frame": frame_info(cloud), "points_in_region": res["count"],
           "count_is_exact": res["count_is_exact"]}
    if res["sampled"]:
        out["sampling"] = (f"Região com {res['candidate_points']:,} pontos candidatos; lidos 1 a cada {res['stride']} "
                           "pontos (nível de detalhe). Contagem e estatísticas são estimativas.")
    return out


def _zstats(z: np.ndarray) -> dict[str, Any] | None:
    if not len(z):
        return None
    p = np.percentile(z, [5, 50, 95])
    return {"min": float(z.min()), "max": float(z.max()), "mean": float(z.mean()), "std": float(z.std()),
            "p05": float(p[0]), "p50": float(p[1]), "p95": float(p[2])}


# ---------------------------------------------------------------------------- ferramentas

def info(cloud: CloudIndex) -> dict[str, Any]:
    m = cloud.m
    counts = [t["count"] for t in m["tiles"].values()]
    return {
        "ok": True, "total_points": m["total_points"], "bounds": m["bounds"],
        "size": (np.asarray(m["bounds"]["max"]) - np.asarray(m["bounds"]["min"])).round(6).tolist(),
        "attributes": ["xyz"] + (["intensity"] if m["has_intensity"] else []) + (["rgb"] if m["has_rgb"] else []),
        "coordinate_frame": frame_info(cloud), "sources": m["sources"],
        "index": {"key": m["key"], "folder": str(cloud.folder), "created": m["created"], "tiles": len(counts),
                  "tile_size_m": m["tile_size_m"], "max_points_per_tile": max(counts) if counts else 0},
    }


def query_box(cloud: CloudIndex, bmin, bmax, sample_size: int = DEFAULT_SAMPLE) -> dict[str, Any]:
    k = _sample_size(sample_size)
    res = cloud.read(box_region(cloud, bmin, bmax))
    p = res["points"]
    out = _base(cloud, res)
    if len(p["x"]):
        xyz = np.column_stack([p["x"], p["y"], p["z"]])
        out["actual_bounds"] = {"min": xyz.min(axis=0).round(6).tolist(), "max": xyz.max(axis=0).round(6).tolist()}
    else:
        out["actual_bounds"] = None
    out["z_stats"] = _zstats(p["z"])
    idx = _pick(len(p["x"]), k)
    out["sample"] = {"size": len(idx), "method": "aleatória uniforme (semente fixa)",
                     **_rows(p, idx, cloud, ["x", "y", "z"])}
    return {"ok": True, **out}


def z_histogram(cloud: CloudIndex, bmin=None, bmax=None, bin_size_m: float = 0.05) -> dict[str, Any]:
    bs = _positive(bin_size_m, "bin_size_m", 1000)
    res = cloud.read(box_region(cloud, bmin, bmax))
    z = res["points"]["z"]
    out = _base(cloud, res)
    if not len(z):
        return {"ok": True, **out, "bins": [], "peaks": []}
    lo = math.floor(z.min() / bs) * bs
    nb = math.ceil((z.max() - lo) / bs) + 1
    if nb > MAX_BINS:
        raise errors.RecapMcpError(errors.LIMIT_EXCEEDED, f"{nb} intervalos excedem o limite de {MAX_BINS}; aumente bin_size_m.")
    counts, edges = np.histogram(z, bins=nb, range=(lo, lo + nb * bs))
    scale = res["stride"]
    counts = counts * scale
    # picos: máximos locais acima de média + 1 desvio (pisos, lajes, forros)
    peaks = []
    thr = counts.mean() + counts.std()
    for j in range(len(counts)):
        left = counts[j - 1] if j else -1
        right = counts[j + 1] if j + 1 < len(counts) else -1
        if counts[j] >= thr and counts[j] >= left and counts[j] > right:
            peaks.append({"z_center": round(float(edges[j] + bs / 2), 4), "count": int(counts[j])})
    # refina cada pico pela mediana dos Z do intervalo e dos vizinhos (a superfície pode cair na divisa)
    for pk in peaks:
        j = round((pk["z_center"] - bs / 2 - lo) / bs)
        a, b = edges[max(j - 1, 0)], edges[min(j + 2, len(edges) - 1)]
        sel = z[(z >= a) & (z <= b)]
        pk["z_estimate"] = round(float(np.median(sel)), 4) if len(sel) else pk["z_center"]
    peaks.sort(key=lambda d: -d["count"])
    out["bin_size_m"] = bs
    out["bins"] = [{"z_from": round(float(edges[j]), 4), "z_to": round(float(edges[j + 1]), 4), "count": int(c)}
                   for j, c in enumerate(counts)]
    out["peaks"] = peaks[:15]
    out["peaks_note"] = ("Picos = intervalos com concentração de pontos (candidatos a pisos, lajes e forros). "
                         "Confirme com section/render_view.")
    return {"ok": True, **out}


def _image_payload(png: bytes, meta: dict, legend: dict) -> dict[str, Any]:
    return {"png": png, "image": {**meta, "legend": legend}}


def section(cloud: CloudIndex, mode: str, thickness_m: float, z: float | None = None,
            p1=None, p2=None, bmin=None, bmax=None, sample_size: int = DEFAULT_SAMPLE,
            color_by: str = "elevation", width_px: int = 1200, point_size: int = 2) -> dict[str, Any]:
    t = _positive(thickness_m, "thickness_m", 100)
    k = _sample_size(sample_size)
    base = box_region(cloud, bmin, bmax)
    if mode == "horizontal":
        if z is None:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "Seção horizontal exige 'z' (cota).")
        zc = float(z)
        reg = Region(np.r_[base.min[:2], zc - t / 2], np.r_[base.max[:2], zc + t / 2])
        res = cloud.read(reg)
        p = res["points"]
        u, v, depth = p["x"], p["y"], -p["z"]
        colors, legend = colorize(p, color_by, cloud.m["has_rgb"], cloud.m["has_intensity"])
        ext = (float(reg.min[0]), float(reg.max[0]), float(reg.min[1]), float(reg.max[1]))
        if len(u):
            ext = (float(u.min()), float(u.max()), float(v.min()), float(v.max()))
        title = f"Seção horizontal z = {zc:.3f} ± {t / 2:.3f}"
        xa, ya = Axis("X"), Axis("Y")
        cols2d = {"x": p["x"], "y": p["y"]}
        names = ["x", "y"]
        geom = {"mode": "horizontal", "z": zc, "thickness_m": t}
    elif mode == "vertical":
        a, b = _vec(p1, 2, "p1"), _vec(p2, 2, "p2")
        d = b - a
        length = float(np.hypot(*d))
        if length <= 0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "p1 e p2 devem ser diferentes.")
        dirv = d / length
        nrm = np.array([-dirv[1], dirv[0]])
        corners = np.array([a + nrm * t / 2, a - nrm * t / 2, b + nrm * t / 2, b - nrm * t / 2])

        def pred(x, y, zz, a=a, dirv=dirv, nrm=nrm, length=length, t=t):
            rx, ry = x - a[0], y - a[1]
            s = rx * dirv[0] + ry * dirv[1]
            off = rx * nrm[0] + ry * nrm[1]
            return (s >= -EDGE_TOL) & (s <= length + EDGE_TOL) & (np.abs(off) <= t / 2 + EDGE_TOL)

        reg = Region(np.r_[corners.min(axis=0), base.min[2]], np.r_[corners.max(axis=0), base.max[2]], pred)
        res = cloud.read(reg)
        p = res["points"]
        rx, ry = p["x"] - a[0], p["y"] - a[1]
        s = rx * dirv[0] + ry * dirv[1]
        off = rx * nrm[0] + ry * nrm[1]
        u, v, depth = s, p["z"], -off
        colors, legend = colorize(p, color_by, cloud.m["has_rgb"], cloud.m["has_intensity"])
        ext = (0.0, length, float(reg.min[2]), float(reg.max[2]))
        if len(v):
            ext = (0.0, length, float(v.min()), float(v.max()))
        title = f"Seção vertical de ({a[0]:.2f}, {a[1]:.2f}) a ({b[0]:.2f}, {b[1]:.2f}), espessura {t:.3f}"
        xa, ya = Axis("distância ao longo do corte"), Axis("Z")
        cols2d = {"s": s, "z": p["z"]}
        names = ["s", "z"]
        geom = {"mode": "vertical", "p1": a.tolist(), "p2": b.tolist(), "length_m": length, "thickness_m": t,
                "s_axis": "distância de p1 ao ponto, medida ao longo de p1→p2"}
    else:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "mode deve ser 'horizontal' ou 'vertical'.")
    out = _base(cloud, res)
    idx = _pick(len(u), k)
    pts2 = {**cols2d, **{c: p[c] for c in ("i", "r", "g", "b")}}
    out["section"] = geom
    out["points_2d"] = {"size": len(idx), **_rows(pts2, idx, cloud, names)}
    png, meta = raster(u, v, depth, colors, ext, width_px, point_size, title, xa, ya, legend,
                       unit=_unit(cloud))
    return {"ok": True, **out, **_image_payload(png, meta, legend)}


def _unit(cloud: CloudIndex) -> str:
    u = frame_info(cloud)["unit"]
    return "m" if isinstance(u, str) and u.startswith("m") else "unid."


VIEWS = {
    # nome: (eixo u, sinal u, eixo v, sinal v, eixo de profundidade, sinal) — profundidade menor = mais perto
    "top": ("x", 1, "y", 1, "z", -1),
    "bottom": ("x", 1, "y", -1, "z", 1),
    "front": ("x", 1, "z", 1, "y", 1),
    "back": ("x", -1, "z", 1, "y", -1),
    "left": ("y", -1, "z", 1, "x", 1),
    "right": ("y", 1, "z", 1, "x", -1),
}
COLOR_PT = {"rgb": "RGB", "intensity": "intensidade", "elevation": "elevação"}
VIEW_NAMES_PT = {"top": "topo", "bottom": "de baixo", "front": "frente (olhando +Y)", "back": "fundos (olhando -Y)",
                 "left": "lateral esquerda (olhando +X)", "right": "lateral direita (olhando -X)"}


def render_view(cloud: CloudIndex, view: str = "top", direction=None, bmin=None, bmax=None,
                color_by: str = "rgb", width_px: int = 1200, point_size: int = 1) -> dict[str, Any]:
    reg = box_region(cloud, bmin, bmax)
    # uma imagem de até 4096 px não precisa de mais pontos que isso; acima, lê 1 a cada N (nível de detalhe)
    res = cloud.read(reg, max_points=min(RENDER_MAX_POINTS, MAX_QUERY_POINTS))
    p = res["points"]
    colors, legend = colorize(p, color_by, cloud.m["has_rgb"], cloud.m["has_intensity"])
    if view in VIEWS:
        ua, us, va, vs, da, ds = VIEWS[view]
        u, v, depth = us * p[ua], vs * p[va], ds * p[da]
        bmn, bmx = reg.min, reg.max
        ax = {"x": 0, "y": 1, "z": 2}
        ue = sorted((us * bmn[ax[ua]], us * bmx[ax[ua]]))
        ve = sorted((vs * bmn[ax[va]], vs * bmx[ax[va]]))
        if len(u):
            ue, ve = (float(u.min()), float(u.max())), (float(v.min()), float(v.max()))
        xa, ya = Axis(ua.upper(), us), Axis(va.upper(), vs)
        vname = VIEW_NAMES_PT[view]
    elif view == "custom":
        dvec = _vec(direction, 3, "direction")
        nd = np.linalg.norm(dvec)
        if nd == 0:
            raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "direction não pode ser nula.")
        dvec = dvec / nd
        up = np.array([0.0, 0.0, 1.0]) if abs(dvec[2]) < 0.99 else np.array([0.0, 1.0, 0.0])
        uvec = np.cross(dvec, up)
        uvec /= np.linalg.norm(uvec)
        vvec = np.cross(uvec, dvec)
        xyz = np.column_stack([p["x"], p["y"], p["z"]])
        u, v, depth = xyz @ uvec, xyz @ vvec, xyz @ dvec
        ue = (float(u.min()), float(u.max())) if len(u) else (0.0, 1.0)
        ve = (float(v.min()), float(v.max())) if len(v) else (0.0, 1.0)
        xa, ya = Axis("u (horizontal da tela)"), Axis("v (vertical da tela)")
        vname = f"direção {np.round(dvec, 3).tolist()}"
    else:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "view deve ser top, bottom, front, back, left, right ou custom.")
    title = f"Vista {vname} — cor: {COLOR_PT.get(legend['color_by'], legend['color_by'])}"
    if res["sampled"]:
        title += f" — 1 a cada {res['stride']} pontos"
    png, meta = raster(u, v, depth, colors, (ue[0], ue[1], ve[0], ve[1]), width_px, point_size, title, xa, ya,
                       legend, unit=_unit(cloud))
    out = _base(cloud, res)
    out["view"] = {"name": view, "direction": None if view != "custom" else np.round(dvec, 6).tolist(),
                   "axes": {"horizontal": xa.label, "vertical": ya.label},
                   "region": {"min": reg.min.tolist(), "max": reg.max.tolist()}}
    if view == "custom":
        out["view"]["screen_basis"] = {"u": np.round(uvec, 6).tolist(), "v": np.round(vvec, 6).tolist()}
    return {"ok": True, **out, **_image_payload(png, meta, legend)}


def profile(cloud: CloudIndex, polyline, width_m: float, step_m: float = 0.5, sample_size: int = DEFAULT_SAMPLE,
            zmin: float | None = None, zmax: float | None = None, width_px: int = 1200) -> dict[str, Any]:
    w = _positive(width_m, "width_m", 1000)
    st = _positive(step_m, "step_m", 10000)
    k = _sample_size(sample_size)
    if not isinstance(polyline, list) or len(polyline) < 2 or len(polyline) > 500:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "polyline deve ter de 2 a 500 vértices [x, y].")
    verts = np.array([_vec(q, 2, "polyline[i]") for q in polyline])
    seg = np.diff(verts, axis=0)
    lens = np.hypot(seg[:, 0], seg[:, 1])
    if np.any(lens == 0):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "polyline tem vértices repetidos consecutivos.")
    cum = np.r_[0, np.cumsum(lens)]
    cmn, cmx = cloud.bounds
    z0 = cmn[2] if zmin is None else float(zmin)
    z1 = cmx[2] if zmax is None else float(zmax)
    ch_all, z_all, off_all, keep = [], [], [], {c: [] for c in ("i", "r", "g", "b")}
    total = 0
    exact = True
    strides = []
    for j in range(len(seg)):
        a, dvec = verts[j], seg[j] / lens[j]
        nrm = np.array([-dvec[1], dvec[0]])
        L = lens[j]
        corners = np.array([a + nrm * w / 2, a - nrm * w / 2, a + seg[j] + nrm * w / 2, a + seg[j] - nrm * w / 2])

        last = j == len(seg) - 1

        def pred(x, y, zz, a=a, dvec=dvec, nrm=nrm, L=L, last=last):
            rx, ry = x - a[0], y - a[1]
            s = rx * dvec[0] + ry * dvec[1]
            # trechos semiabertos [0, L) para não contar duas vezes o vértice; o último inclui a extremidade
            end = (s <= L + EDGE_TOL) if last else (s < L)
            return (s >= -EDGE_TOL) & end & (np.abs(rx * nrm[0] + ry * nrm[1]) <= w / 2 + EDGE_TOL)

        res = cloud.read(Region(np.r_[corners.min(axis=0), z0], np.r_[corners.max(axis=0), z1], pred))
        p = res["points"]
        rx, ry = p["x"] - a[0], p["y"] - a[1]
        ch_all.append(cum[j] + rx * dvec[0] + ry * dvec[1])
        off_all.append(rx * nrm[0] + ry * nrm[1])
        z_all.append(p["z"])
        for c, lst in keep.items():
            lst.append(p[c])
        total += res["count"]
        exact &= res["count_is_exact"]
        strides.append(res["stride"])
    ch, z = np.concatenate(ch_all), np.concatenate(z_all)
    pts = {"chainage": ch, "z": z, **{c: np.concatenate(v) for c, v in keep.items()}}
    nb = max(1, math.ceil(cum[-1] / st))
    if nb > MAX_BINS:
        raise errors.RecapMcpError(errors.LIMIT_EXCEEDED, f"step_m gera {nb} intervalos; máximo {MAX_BINS}.")
    stations = []
    for q in range(nb):
        m = (ch >= q * st) & (ch < (q + 1) * st)
        if m.any():
            zz = z[m]
            stations.append({"from": round(q * st, 4), "to": round(min((q + 1) * st, cum[-1]), 4),
                             "count": int(m.sum()), "z_min": round(float(zz.min()), 4),
                             "z_p50": round(float(np.median(zz)), 4), "z_max": round(float(zz.max()), 4)})
        else:
            stations.append({"from": round(q * st, 4), "to": round(min((q + 1) * st, cum[-1]), 4), "count": 0})
    idx = _pick(len(ch), k)
    colors, legend = colorize({"z": z, **{c: pts[c] for c in ("i", "r", "g", "b")}, "x": ch},
                              "elevation", cloud.m["has_rgb"], cloud.m["has_intensity"])
    ext = (0.0, float(cum[-1]), float(z.min()) if len(z) else z0, float(z.max()) if len(z) else z1)
    png, meta = raster(ch, z, np.abs(np.concatenate(off_all)), colors, ext, width_px, 2,
                       f"Perfil ao longo da polilinha ({cum[-1]:.2f} {_unit(cloud)}), faixa {w:.2f}",
                       Axis("estaca (distância ao longo da polilinha)"), Axis("Z"), legend, unit=_unit(cloud))
    out = {"coordinate_frame": frame_info(cloud), "points_in_corridor": int(total), "count_is_exact": exact,
           "polyline_length": float(cum[-1]), "width_m": w, "step_m": st, "stations": stations,
           "points_2d": {"size": len(idx), **_rows(pts, idx, cloud, ["chainage", "z"])}}
    if max(strides) > 1:
        out["sampling"] = f"Parte do corredor lida com passo de até {max(strides)} (nível de detalhe)."
    return {"ok": True, **out, **_image_payload(png, meta, legend)}


def nearest_points(cloud: CloudIndex, point, k: int = 10, max_radius_m: float = 0.5) -> dict[str, Any]:
    c = _vec(point, 3, "point")
    r = _positive(max_radius_m, "max_radius_m", 100)
    if not isinstance(k, int) or not (1 <= k <= 100):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "k deve estar entre 1 e 100.")
    res = cloud.read(Region(c - r, c + r))
    p = res["points"]
    d = np.sqrt((p["x"] - c[0]) ** 2 + (p["y"] - c[1]) ** 2 + (p["z"] - c[2]) ** 2)
    within = np.flatnonzero(d <= r)
    order = within[np.argsort(d[within])][:k]
    rows = _rows(p, order, cloud, ["x", "y", "z"])
    rows["columns"].append("distance")
    for row, dist in zip(rows["rows"], d[order]):
        row.append(round(float(dist), 5))
    out = {"coordinate_frame": frame_info(cloud), "query_point": c.tolist(), "max_radius_m": r,
           "found": len(order), "nearest": rows}
    if res["sampled"]:
        out["sampling"] = f"Região densa: lidos 1 a cada {res['stride']} pontos; os vizinhos podem não ser os exatos."
    if not len(order):
        out["note"] = "Nenhum ponto dentro do raio; aumente max_radius_m."
    return {"ok": True, **out}


def measure(cloud: CloudIndex, p1, p2, snap: bool = True, snap_radius_m: float = 0.05) -> dict[str, Any]:
    a, b = _vec(p1, 3, "p1"), _vec(p2, 3, "p2")
    snapped = {}
    if snap:
        for name, q in (("p1", a), ("p2", b)):
            nn = nearest_points(cloud, q.tolist(), k=1, max_radius_m=snap_radius_m)
            if nn["found"]:
                row = nn["nearest"]["rows"][0]
                snapped[name] = {"point": row[:3], "moved_by": row[-1]}
            else:
                snapped[name] = None
        if snapped["p1"]:
            a = np.asarray(snapped["p1"]["point"])
        if snapped["p2"]:
            b = np.asarray(snapped["p2"]["point"])
    d = b - a
    return {"ok": True, "coordinate_frame": frame_info(cloud), "p1": a.tolist(), "p2": b.tolist(),
            "distance_3d": round(float(np.linalg.norm(d)), 5), "distance_horizontal": round(float(np.hypot(d[0], d[1])), 5),
            "delta": np.round(d, 5).tolist(), "snap": snapped if snap else None}
