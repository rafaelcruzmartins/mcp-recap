"""Índice espacial em blocos (tiles XY) gravado como cache derivado.

Os formatos de origem (E57, LAS, PTS) não têm índice espacial. A indexação lê a nuvem uma
vez e grava blocos binários em ``<estado>/cache/<chave>/``. A chave depende do caminho,
tamanho e data de cada arquivo de origem: se a origem mudar, o cache é refeito.

Consultas leem só os blocos que tocam a região. Quando a região tem mais pontos que o
orçamento, cada bloco é lido com passo ``k`` (1 a cada ``k`` pontos): um nível de detalhe
uniforme, sinalizado na resposta como amostrado e com contagem estimada.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .. import errors
from .sources import SourceFile, iter_chunks, resolve_sources

CACHE_VERSION = 1
TILE_DTYPE = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("i", "<f4"),
                       ("r", "u1"), ("g", "u1"), ("b", "u1")])
TARGET_POINTS_PER_TILE = 2_000_000
FLUSH_POINTS = 4_000_000
SYNC_INDEX_MAX_POINTS = int(os.environ.get("RECAP_MCP_SYNC_INDEX_POINTS", "3000000"))
MAX_QUERY_POINTS = int(os.environ.get("RECAP_MCP_MAX_QUERY_POINTS", "10000000"))
QUERY_TIMEOUT_S = float(os.environ.get("RECAP_MCP_QUERY_TIMEOUT_S", "120"))
CACHE_REQUIRED = "cache_required"
# o cache guarda coordenadas em precisão simples relativas ao bloco (erro < 0,1 mm em blocos de até 1 km);
# a tolerância evita perder pontos exatamente sobre os limites da consulta
EDGE_TOL = 1e-4


from ..jobs import TaskCancelled as Cancelled


def cache_root(state_dir: Path) -> Path:
    return Path(state_dir) / "cache"


def cache_key(sources: list[SourceFile], input_unit: str) -> str:
    h = hashlib.sha1(f"v{CACHE_VERSION}|{input_unit}".encode())
    for fp in sorted(s.fingerprint() for s in sources):
        h.update(fp.encode("utf-8"))
    return h.hexdigest()[:20]


def estimate_points(sources: list[SourceFile]) -> int:
    total = 0
    for s in sources:
        if s.point_count is not None:
            total += s.point_count
        else:  # texto sem contagem: estima pelo tamanho médio das primeiras linhas
            with s.path.open("rb") as fh:
                head = fh.read(65536)
            lines = max(1, head.count(b"\n"))
            total += int(s.path.stat().st_size / (len(head) / lines))
    return total


# ---------------------------------------------------------------------------- construção

def build_index(state_dir: Path, sources: list[SourceFile], context: dict[str, Any], input_unit: str,
                progress: Callable[[float, str], None] | None = None,
                cancelled: Callable[[], bool] | None = None,
                tile_size_m: float | None = None) -> Path:
    progress = progress or (lambda p, m: None)
    cancelled = cancelled or (lambda: False)
    key = cache_key(sources, input_unit)
    final = cache_root(state_dir) / key
    if (final / "manifest.json").is_file():
        return final
    tmp = cache_root(state_dir) / f"{key}.tmp-{os.getpid()}-{int(time.time())}"
    tmp.mkdir(parents=True)
    try:
        est = estimate_points(sources)
        need = est * TILE_DTYPE.itemsize * 1.1
        free = shutil.disk_usage(tmp).free
        if need > free:
            raise errors.RecapMcpError(errors.LIMIT_EXCEEDED,
                                       f"Espaço insuficiente para o índice: ~{need / 1e9:.1f} GB necessários, "
                                       f"{free / 1e9:.1f} GB livres.", cache_dir=str(tmp.parent))
        # passo 1: limites e contagem (necessário para dimensionar os blocos)
        mn = np.full(3, np.inf)
        mx = np.full(3, -np.inf)
        count = 0
        attrs = {"i": False, "rgb": False}
        for si, s in enumerate(sources):
            for ch in iter_chunks(s):
                if cancelled():
                    raise Cancelled()
                if len(ch["x"]):
                    xyz = np.column_stack([ch["x"], ch["y"], ch["z"]])
                    mn = np.minimum(mn, xyz.min(axis=0))
                    mx = np.maximum(mx, xyz.max(axis=0))
                count += len(ch["x"])
                attrs["i"] |= "i" in ch
                attrs["rgb"] |= "r" in ch
                progress(min(30.0, 30.0 * count / max(est, 1)), f"passo 1/2: {count:,} pontos lidos ({si + 1}/{len(sources)})")
        if count == 0:
            raise errors.RecapMcpError(errors.INVALID_PROJECT, "Nenhum ponto válido nos arquivos de origem.")
        dx, dy = max(mx[0] - mn[0], 1e-6), max(mx[1] - mn[1], 1e-6)
        ts = tile_size_m or math.sqrt(dx * dy * TARGET_POINTS_PER_TILE / count)
        ts = float(min(max(ts, 0.5), 1000.0))
        origin = np.floor(mn[:2] / ts) * ts

        tiles: dict[str, dict[str, Any]] = {}
        buf: dict[str, list[np.ndarray]] = {}
        buffered = 0
        tdir = tmp / "tiles"
        tdir.mkdir()

        def flush() -> None:
            nonlocal buffered
            for tid, parts in buf.items():
                with open(tdir / f"{tid}.bin", "ab") as fh:
                    fh.writelines(p.tobytes() for p in parts)
            buf.clear()
            buffered = 0

        done = 0
        for s in sources:
            for ch in iter_chunks(s):
                if cancelled():
                    raise Cancelled()
                n = len(ch["x"])
                if n == 0:
                    continue
                ix = np.floor((ch["x"] - origin[0]) / ts).astype(np.int64)
                iy = np.floor((ch["y"] - origin[1]) / ts).astype(np.int64)
                keyv = ix * 1_000_003 + iy
                order = np.argsort(keyv, kind="stable")
                ks = keyv[order]
                starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
                ends = np.r_[starts[1:], len(ks)]
                for a, b in zip(starts, ends):
                    idx = order[a:b]
                    tx, ty = int(ix[idx[0]]), int(iy[idx[0]])
                    tid = f"{tx}_{ty}"
                    ox, oy = origin[0] + tx * ts, origin[1] + ty * ts
                    rec = np.zeros(len(idx), dtype=TILE_DTYPE)
                    rec["x"] = ch["x"][idx] - ox
                    rec["y"] = ch["y"][idx] - oy
                    rec["z"] = ch["z"][idx]
                    if "i" in ch:
                        rec["i"] = ch["i"][idx]
                    if "r" in ch:
                        rec["r"], rec["g"], rec["b"] = ch["r"][idx], ch["g"][idx], ch["b"][idx]
                    t = tiles.setdefault(tid, {"ix": tx, "iy": ty, "count": 0,
                                               "zmin": math.inf, "zmax": -math.inf})
                    t["count"] += len(idx)
                    t["zmin"] = min(t["zmin"], float(ch["z"][idx].min()))
                    t["zmax"] = max(t["zmax"], float(ch["z"][idx].max()))
                    buf.setdefault(tid, []).append(rec)
                    buffered += len(idx)
                done += n
                if buffered >= FLUSH_POINTS:
                    flush()
                progress(30.0 + 69.0 * done / count, f"passo 2/2: {done:,} de {count:,} pontos")
        flush()
        manifest = {
            "cache_version": CACHE_VERSION, "key": key, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "input_unit": input_unit, "sources": [s.to_dict() for s in sources], "context": context,
            "total_points": int(count), "bounds": {"min": mn.tolist(), "max": mx.tolist()},
            "has_intensity": attrs["i"], "has_rgb": attrs["rgb"], "tile_size_m": ts,
            "origin_xy": origin.tolist(), "tiles": tiles,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        try:
            os.replace(tmp, final)
        except OSError:
            if (final / "manifest.json").is_file():  # outra chamada terminou primeiro
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                raise
        progress(100.0, "índice concluído")
        return final
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------- consulta

@dataclass
class Region:
    """Caixa envolvente (para escolher blocos) e filtro opcional sobre os pontos."""
    min: np.ndarray
    max: np.ndarray
    predicate: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray] | None = None


class CloudIndex:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.m = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        self.ts = float(self.m["tile_size_m"])
        self.origin = np.asarray(self.m["origin_xy"], dtype=float)

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(self.m["bounds"]["min"]), np.asarray(self.m["bounds"]["max"])

    def full_region(self) -> Region:
        mn, mx = self.bounds
        return Region(mn, mx)

    def _tile_box(self, t: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        ox = self.origin[0] + t["ix"] * self.ts
        oy = self.origin[1] + t["iy"] * self.ts
        return np.array([ox, oy, t["zmin"]]), np.array([ox + self.ts, oy + self.ts, t["zmax"]])

    def candidate_tiles(self, region: Region) -> list[tuple[str, dict[str, Any], bool]]:
        out = []
        for tid, t in self.m["tiles"].items():
            tmn, tmx = self._tile_box(t)
            if np.any(tmx < region.min) or np.any(tmn > region.max):
                continue
            inside = bool(np.all(tmn >= region.min) and np.all(tmx <= region.max) and region.predicate is None)
            out.append((tid, t, inside))
        return out

    def read(self, region: Region, max_points: int = MAX_QUERY_POINTS, timeout_s: float = QUERY_TIMEOUT_S,
             cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Lê os pontos da região com orçamento de memória e de tempo."""
        cands = self.candidate_tiles(region)
        candidate_points = sum(t["count"] for _, t, _ in cands)
        stride = max(1, math.ceil(candidate_points / max_points)) if candidate_points else 1
        deadline = time.monotonic() + timeout_s
        parts: dict[str, list[np.ndarray]] = {k: [] for k in ("x", "y", "z", "i", "r", "g", "b")}
        exact_inside = 0
        sampled_matches = 0
        for tid, t, inside in cands:
            if cancelled and cancelled():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise errors.RecapMcpError(
                    errors.LIMIT_EXCEEDED, f"Consulta excedeu {timeout_s:.0f} s. Reduza a região ou aumente "
                    "RECAP_MCP_QUERY_TIMEOUT_S.", candidate_points=candidate_points)
            mm = np.memmap(self.folder / "tiles" / f"{tid}.bin", dtype=TILE_DTYPE, mode="r")
            rec = np.array(mm[::stride])
            del mm
            ox = self.origin[0] + t["ix"] * self.ts
            oy = self.origin[1] + t["iy"] * self.ts
            x = rec["x"].astype(np.float64) + ox
            y = rec["y"].astype(np.float64) + oy
            z = rec["z"].astype(np.float64)
            lo, hi = region.min - EDGE_TOL, region.max + EDGE_TOL
            mask = ((x >= lo[0]) & (x <= hi[0]) & (y >= lo[1]) & (y <= hi[1]) & (z >= lo[2]) & (z <= hi[2]))
            if region.predicate is not None:
                mask &= region.predicate(x, y, z)
            if inside:
                exact_inside += t["count"]
            else:
                sampled_matches += int(mask.sum())
            for k, arr in (("x", x), ("y", y), ("z", z), ("i", rec["i"]), ("r", rec["r"]), ("g", rec["g"]),
                           ("b", rec["b"])):
                parts[k].append(arr[mask])
        pts = {k: (np.concatenate(v) if v else np.empty(0)) for k, v in parts.items()}
        sampled = stride > 1
        count = exact_inside + sampled_matches * stride if sampled else exact_inside + sampled_matches
        return {"points": pts, "stride": stride, "sampled": sampled, "count": int(count),
                "count_is_exact": not sampled, "tiles_read": len(cands), "candidate_points": int(candidate_points)}


    def iter_region(self, region: Region, cancelled: Callable[[], bool] | None = None):
        """Percorre a região bloco a bloco, sem amostragem (para exportação)."""
        cands = self.candidate_tiles(region)
        total = sum(t["count"] for _, t, _ in cands) or 1
        done = 0
        for tid, t, _ in cands:
            if cancelled and cancelled():
                raise Cancelled()
            rec = np.fromfile(self.folder / "tiles" / f"{tid}.bin", dtype=TILE_DTYPE)
            x = rec["x"].astype(np.float64) + self.origin[0] + t["ix"] * self.ts
            y = rec["y"].astype(np.float64) + self.origin[1] + t["iy"] * self.ts
            z = rec["z"].astype(np.float64)
            lo, hi = region.min - EDGE_TOL, region.max + EDGE_TOL
            mask = ((x >= lo[0]) & (x <= hi[0]) & (y >= lo[1]) & (y <= hi[1]) & (z >= lo[2]) & (z <= hi[2]))
            if region.predicate is not None:
                mask &= region.predicate(x, y, z)
            done += t["count"]
            yield ({"x": x[mask], "y": y[mask], "z": z[mask], "i": rec["i"][mask], "r": rec["r"][mask],
                    "g": rec["g"][mask], "b": rec["b"][mask]}, done / total)


# ---------------------------------------------------------------------------- acesso

def open_cloud(state_dir: Path, source: str, input_unit: str = "m", build_if_small: bool = True) -> CloudIndex:
    """Abre o índice da origem; constrói na hora se a nuvem for pequena."""
    sources, context = resolve_sources(source, input_unit)
    key = cache_key(sources, input_unit)
    folder = cache_root(state_dir) / key
    if (folder / "manifest.json").is_file():
        return CloudIndex(folder)
    est = estimate_points(sources)
    if build_if_small and est <= SYNC_INDEX_MAX_POINTS:
        return CloudIndex(build_index(state_dir, sources, context, input_unit))
    raise errors.RecapMcpError(
        CACHE_REQUIRED,
        f"A nuvem tem cerca de {est:,} pontos e ainda não foi indexada. Rode index_point_cloud (job assíncrono) "
        "e repita a consulta quando o job terminar.", estimated_points=est)
