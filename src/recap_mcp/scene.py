"""Cena de visualização: um projeto .rcp (ou um .rcs avulso) aberto para ver e navegar.

Reúne as folhas de todos os scans numa tabela única, em coordenadas da cena (coordenadas
do projeto menos uma origem, para caber em float32), e entrega amostras de pontos por
orçamento: cada folha contribui com o início do seu bloco, proporcional ao espaço que
ocupa na imagem pedida.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import errors, projects
from .rcs import POINT, RcsFile, unpack_xyz

CACHE_POINTS = 24_000_000  # ~384 MB de pontos brutos em memória


def rotation_matrix(deg: tuple[float, float, float]) -> np.ndarray | None:
    """Matriz do nó ``<Rotation x y z>`` do .rcp: ângulos em graus, R = Rz · Ry · Rx.

    O giro em Z (graus, anti-horário visto de cima) foi conferido sobrepondo scans estáticos girados
    a scans móveis sem rotação: 1,5 cm de distância mediana, contra 26 cm com o sinal trocado. Os
    giros em X e Y dos dados disponíveis são menores que 0,1 grau; a ordem adotada é a de melhor
    ajuste, por margem pequena (ver docs/formato-rcs.md).
    """
    if all(abs(a) < 1e-12 for a in deg):
        return None
    rx, ry, rz = (math.radians(a) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


@dataclass
class Scan:
    name: str
    file: RcsFile
    translation: np.ndarray          # coordenadas do projeto (float64)
    rotation_deg: tuple[float, float, float]
    matrix: np.ndarray | None


def _xyz_attr(el: Any, default: float) -> tuple[float, float, float]:
    if el is None:
        return (default, default, default)
    return tuple(float(el.get(a, default)) for a in "xyz")  # type: ignore[return-value]


def _value(node: Any, tag: str) -> str | None:
    el = node.find(tag)
    return None if el is None else el.get("Value")


class Scene:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.warnings: list[str] = []
        self.scans: list[Scan] = []
        self.empty_scans = 0
        self.missing: list[str] = []
        ext = self.path.suffix.lower()
        if ext == ".rcs":
            f = RcsFile(self.path)
            self._add_scan(self.path.stem, f, f.translation, tuple(f.rotation_deg), f.scale)
        elif ext == ".rcp":
            self._load_project()
        else:
            raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT,
                                       f"Extensão '{self.path.suffix}' não suportada; use .rcp ou .rcs.",
                                       path=str(self.path))
        if not self.scans:
            raise errors.RecapMcpError(
                errors.INVALID_PROJECT, "Nenhum scan com pontos foi encontrado neste arquivo.",
                path=str(self.path), missing_rcs=self.missing[:20] or None)
        self._build_tables()
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._cached_points = 0
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ carga

    def _add_scan(self, name: str, f: RcsFile, translation: Any, rot: tuple, scale: tuple) -> None:
        if f.num_points == 0:
            self.empty_scans += 1
            f.close()
            return
        if any(abs(s - 1.0) > 1e-9 for s in scale):
            self.warnings.append(f"Scan '{name}' tem escala {scale}; a escala não é aplicada.")
        self.scans.append(Scan(name, f, np.asarray(translation, np.float64), tuple(rot), rotation_matrix(tuple(rot))))

    def _load_project(self) -> None:
        root, _ = projects.load_manifest(self.path)
        nodes = root.find("Nodes")
        entries = []
        for node in (nodes if nodes is not None else []):
            if node.tag != "VoxelTreeRunTime":
                continue
            entries.append({
                "name": _value(node, "NodeName") or "", "path": _value(node, "Path"),
                "relative": _value(node, "RelativePath"),
                "translation": _xyz_attr(node.find("Translation"), 0.0),
                "rotation": _xyz_attr(node.find("Rotation"), 0.0),
                "scale": _xyz_attr(node.find("Scale"), 1.0),
            })
        gt = root.find("GlobalTransformation")
        if gt is not None:
            ident = {"m11": 1, "m22": 1, "m33": 1, "m44": 1}
            if any(abs(float(v) - ident.get(k, 0)) > 1e-12 for k, v in gt.attrib.items()):
                self.warnings.append("O projeto tem uma transformação global diferente da identidade; "
                                     "ela não é aplicada às coordenadas mostradas.")

        def open_one(e: dict) -> tuple[dict, RcsFile | None, str | None]:
            res = projects._resolve_rcs(self.path, e["path"], e["relative"])
            if not res["exists"]:
                return e, None, "ausente"
            try:
                return e, RcsFile(Path(res["resolved_path"])), None
            except errors.RecapMcpError as exc:
                return e, None, exc.message

        with ThreadPoolExecutor(max_workers=16) as pool:
            for e, f, problem in pool.map(open_one, entries):
                if f is None:
                    self.missing.append(e["name"])
                    if problem != "ausente":
                        self.warnings.append(f"Scan '{e['name']}' ignorado: {problem}")
                    continue
                self._add_scan(e["name"], f, e["translation"], e["rotation"], e["scale"])
        if self.missing:
            self.warnings.append(f"{len(self.missing)} scan(s) do projeto sem arquivo .rcs legível.")

    def _build_tables(self) -> None:
        self.origin = np.round(self.scans[0].translation)  # origem da cena, em coordenadas do projeto
        scan_id, leaf_id, counts, cmin, cmax, edge, lmin = [], [], [], [], [], [], []
        for s, scan in enumerate(self.scans):
            lv = scan.file.leaves
            n = len(lv)
            rel = (scan.translation - self.origin)
            lo, hi = lv["dmn"], lv["dmx"]
            if scan.matrix is None:
                a, b = lo + rel, hi + rel
            else:
                corners = np.stack([np.where(np.array(m)[None, :], hi, lo)
                                    for m in np.ndindex(2, 2, 2)], 1)          # (n, 8, 3)
                w = corners @ scan.matrix.T + rel
                a, b = w.min(1), w.max(1)
            scan_id.append(np.full(n, s, np.int32))
            leaf_id.append(np.arange(n, dtype=np.int32))
            counts.append(scan.file.counts)
            cmin.append(a)
            cmax.append(b)
            edge.append((lv["mx"] - lv["mn"])[:, 0])
            lmin.append(lv["mn"])
        self.leaf_scan = np.concatenate(scan_id)
        self.leaf_index = np.concatenate(leaf_id)
        self.leaf_count = np.concatenate(counts)
        self.leaf_min = np.concatenate(cmin).astype(np.float32)   # caixa dos dados, coordenadas da cena
        self.leaf_max = np.concatenate(cmax).astype(np.float32)
        self.leaf_edge = np.concatenate(edge).astype(np.float32)
        self.leaf_local_min = np.concatenate(lmin).astype(np.float32)
        self.leaf_center = (self.leaf_min + self.leaf_max) / 2
        self.leaf_radius = np.linalg.norm(self.leaf_max - self.leaf_min, axis=1) / 2
        self.scan_rel = np.stack([s.translation - self.origin for s in self.scans]).astype(np.float32)
        self.bounds_min = self.leaf_min.min(0).astype(np.float64)
        self.bounds_max = self.leaf_max.max(0).astype(np.float64)
        self.num_points = int(self.leaf_count.sum())

    # ------------------------------------------------------------- utilidades

    def to_world(self, p: Any) -> list[float]:
        return [round(float(v), 3) for v in (np.asarray(p, np.float64) + self.origin)]

    def to_scene(self, p: Any) -> np.ndarray:
        return np.asarray(p, np.float64) - self.origin

    def info(self) -> dict[str, Any]:
        rotated = sum(1 for s in self.scans if s.matrix is not None)
        biggest = sorted(self.scans, key=lambda s: -s.file.num_points)[:8]
        return {
            "path": str(self.path),
            "scans_with_points": len(self.scans),
            "scans_without_points": self.empty_scans,
            "rotated_scans": rotated,
            "points": self.num_points,
            "octree_leaves": len(self.leaf_count),
            "bounds": {"min": self.to_world(self.bounds_min), "max": self.to_world(self.bounds_max),
                       "size": [round(float(v), 3) for v in (self.bounds_max - self.bounds_min)]},
            "units": "metros (X, Y, Z do projeto; Z para cima)",
            "largest_scans": [{"name": s.name, "points": s.file.num_points} for s in biggest],
            "warnings": self.warnings,
        }

    def close(self) -> None:
        for s in self.scans:
            s.file.close()

    # ------------------------------------------------------ leitura de pontos

    def _prefix(self, k: int, m: int) -> np.ndarray:
        with self._cache_lock:
            hit = self._cache.get(k)
            if hit is not None and len(hit) >= m:
                self._cache.move_to_end(k)
                return hit[:m]
        total = int(self.leaf_count[k])
        want = min(total, max(m, int(m * 1.5)))   # folga: a próxima vista mais próxima reaproveita
        data = self.scans[int(self.leaf_scan[k])].file.read_points(int(self.leaf_index[k]), want)
        with self._cache_lock:
            old = self._cache.pop(k, None)
            if old is not None:
                self._cached_points -= len(old)
            self._cache[k] = data
            self._cached_points += len(data)
            while self._cached_points > CACHE_POINTS and len(self._cache) > 1:
                _, dropped = self._cache.popitem(last=False)
                self._cached_points -= len(dropped)
        return data[:m]

    def gather(self, leaves: np.ndarray, counts: np.ndarray) -> dict[str, np.ndarray]:
        """Lê ``counts[i]`` pontos de cada folha ``leaves[i]``. Devolve posições na cena e atributos."""
        keep = counts > 0
        leaves, counts = leaves[keep], counts[keep].astype(np.int64)
        if len(leaves) == 0:
            return {"xyz": np.zeros((0, 3), np.float32), "rgb": np.zeros((0, 3), np.uint8),
                    "i": np.zeros(0, np.uint8), "leaf": np.zeros(0, np.int32), "taken": counts, "leaves": leaves}
        # ordem de disco: por arquivo e por posição
        order = np.lexsort((self.leaf_index[leaves], self.leaf_scan[leaves]))
        leaves, counts = leaves[order], counts[order]
        chunks = [self._prefix(int(k), int(m)) for k, m in zip(leaves, counts)]
        taken = np.array([len(c) for c in chunks], np.int64)
        raw = np.concatenate(chunks) if chunks else np.zeros(0, POINT)
        lid = np.repeat(leaves.astype(np.int32), taken)
        xyz = unpack_xyz(raw["q"])
        xyz += self.leaf_local_min[lid]
        sid = self.leaf_scan[lid]
        for s in np.unique(self.leaf_scan[leaves]):
            m = self.scans[int(s)].matrix
            if m is not None:
                sel = sid == s
                xyz[sel] = xyz[sel] @ m.T.astype(np.float32)
        xyz += self.scan_rel[sid]
        rgb = np.stack([raw["r"], raw["g"], raw["b"]], 1)
        return {"xyz": xyz, "rgb": rgb, "i": raw["i"], "leaf": lid, "taken": taken, "leaves": leaves}

    @staticmethod
    def allocate(count: np.ndarray, want: np.ndarray, budget: int, max_scale: float = 1.0,
                 key: np.ndarray | None = None) -> np.ndarray:
        """Pontos por folha: ``min(count, a·want)``, com ``a ≤ max_scale`` escolhido para caber no orçamento.

        A parte fracionária é arredondada por sorteio fixo por folha (``key``): uma folha que mereceria
        0,1 ponto entra em 1 de cada 10 casos. Sem isso, uma vista geral de um projeto com centenas de
        milhares de folhas abriria todas elas para ler um ponto de cada.
        """
        want = np.maximum(want, 0.0)
        ids = np.arange(len(want)) if key is None else key
        u = ((ids.astype(np.float64) + 1.0) * 0.6180339887498949) % 1.0

        def take(a: float) -> np.ndarray:
            w = want * a
            base = np.floor(w)
            return np.minimum(count, base + (u < (w - base))).astype(np.int64)

        top = take(max_scale)
        if top.sum() <= budget:
            return top
        lo, hi = 0.0, max_scale
        for _ in range(48):
            mid = (lo + hi) / 2
            if take(mid).sum() > budget:
                hi = mid
            else:
                lo = mid
        return take(lo)
