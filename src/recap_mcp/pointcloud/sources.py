"""Leitura por blocos dos arquivos de origem de uma nuvem de pontos (Plano B, sem o SDK da Autodesk).

Formatos: E57 (pye57/libE57), LAS/LAZ (laspy + lazrs) e texto PTS/XYZ.
Projetos ``.rcp`` são resolvidos para os arquivos de origem gravados em ``rawScanPath``;
o conteúdo dos ``.rcs`` não é decodificado.

Cada leitor devolve blocos ``dict`` com ``x, y, z`` (float64) e, quando existirem,
``i`` (intensidade, float32) e ``r, g, b`` (uint8). Os arquivos originais só são lidos.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import errors
from ..paths import existing_file

CHUNK_POINTS = 1_000_000
TEXT_EXTS = {".pts", ".xyz", ".txt", ".asc"}
SOURCE_EXTS = {".e57", ".las", ".laz"} | TEXT_EXTS
UNIT_FACTORS = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048, "us_ft": 1200 / 3937}

Chunk = dict[str, np.ndarray]


@dataclass
class SourceFile:
    path: Path
    kind: str                       # e57 | las | text
    frame: str                      # descrição do sistema de coordenadas
    unit: str                       # unidade das coordenadas devolvidas
    georeferenced: bool | None
    point_count: int | None         # None quando o cabeçalho não informa
    bounds: dict[str, list[float]] | None
    attributes: list[str]
    scans: list[dict[str, Any]] = field(default_factory=list)
    crs: str | None = None
    notes: list[str] = field(default_factory=list)
    scale: float = 1.0              # fator aplicado (unidade informada → metro) em arquivos de texto

    def fingerprint(self) -> str:
        st = self.path.stat()
        return f"{self.path}|{st.st_size}|{st.st_mtime_ns}|{self.scale}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "format": self.kind, "coordinate_frame": self.frame, "unit": self.unit,
            "georeferenced": self.georeferenced, "point_count": self.point_count, "bounds": self.bounds,
            "attributes": self.attributes, "scans": self.scans, "crs": self.crs, "notes": self.notes,
        }


# ---------------------------------------------------------------------------- E57

def _e57_open(path: Path):
    import pye57

    try:
        return pye57.E57(str(path))
    except Exception as exc:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, f"E57 ilegível: {exc}", path=str(path)) from exc


def describe_e57(path: Path) -> SourceFile:
    e = _e57_open(path)
    scans = []
    total = 0
    attrs: set[str] = set()
    mins, maxs = [], []
    for i in range(e.scan_count):
        h = e.get_header(i)
        fields = set(h.point_fields)
        total += h.point_count
        if {"cartesianX", "sphericalRange"} & fields:
            attrs.add("xyz")
        if "intensity" in fields:
            attrs.add("intensity")
        if {"colorRed", "colorGreen", "colorBlue"} <= fields:
            attrs.add("rgb")
        pose = None
        if h.has_pose():
            pose = {"rotation_quaternion_wxyz": [float(v) for v in h.rotation],
                    "translation": [float(v) for v in h.translation]}
        try:
            name = h["name"].value()
        except Exception:  # noqa: BLE001 - campo opcional na norma E57
            name = None
        scans.append({"index": i, "name": name, "point_count": int(h.point_count), "pose_scan_to_file": pose,
                      "structured": "rowIndex" in fields})
        try:
            b = h["cartesianBounds"]
            mn = [b["xMinimum"].value(), b["yMinimum"].value(), b["zMinimum"].value()]
            mx = [b["xMaximum"].value(), b["yMaximum"].value(), b["zMaximum"].value()]
            if pose is None:
                mins.append(mn)
                maxs.append(mx)
        except Exception:  # noqa: BLE001 - cartesianBounds é opcional na norma E57
            mins.append(None)
    bounds = None
    if mins and len(mins) == e.scan_count and all(m is not None for m in mins):
        bounds = {"min": np.min(mins, axis=0).tolist(), "max": np.max(maxs, axis=0).tolist()}
    e.close()
    return SourceFile(
        path=path, kind="e57", unit="m", georeferenced=None, point_count=total, bounds=bounds,
        attributes=sorted(attrs), scans=scans,
        frame="arquivo E57 (sistema do arquivo, após aplicar a pose de cada scan)",
        notes=["Unidade em metros, conforme a norma ASTM E57."],
    )


def _e57_chunks(path: Path, chunk: int) -> Iterator[Chunk]:
    from pye57 import libe57
    from pye57.e57 import SUPPORTED_POINT_FIELDS
    from pye57.utils import convert_spherical_to_cartesian
    from pyquaternion import Quaternion

    e = _e57_open(path)
    try:
        for i in range(e.scan_count):
            h = e.get_header(i)
            fields = set(h.point_fields)
            spherical = "sphericalRange" in fields and "cartesianX" not in fields
            want = (["sphericalRange", "sphericalAzimuth", "sphericalElevation"] if spherical
                    else ["cartesianX", "cartesianY", "cartesianZ"])
            invalid = "sphericalInvalidState" if spherical else "cartesianInvalidState"
            for opt in ("intensity", "colorRed", "colorGreen", "colorBlue", invalid):
                if opt in fields and opt in SUPPORTED_POINT_FIELDS:
                    want.append(opt)
            data, buffers = {}, libe57.VectorSourceDestBuffer()
            for f in want:
                arr, buf = e.make_buffer(f, chunk)
                data[f] = arr
                buffers.append(buf)
            rot = Quaternion(h.rotation).rotation_matrix if h.has_pose() else None
            trans = np.asarray(h.translation, dtype=float) if h.has_pose() else None
            imax = None
            if "intensity" in data:
                try:
                    imax = float(h.intensityMaximum)
                except Exception:  # noqa: BLE001
                    imax = None
            reader = h.points.reader(buffers)
            try:
                while True:
                    n = reader.read()
                    if n == 0:
                        break
                    sel = slice(0, n)
                    if spherical:
                        rae = np.column_stack([data["sphericalRange"][sel], data["sphericalAzimuth"][sel],
                                               data["sphericalElevation"][sel]])
                        xyz = convert_spherical_to_cartesian(rae)
                    else:
                        xyz = np.column_stack([data["cartesianX"][sel], data["cartesianY"][sel],
                                               data["cartesianZ"][sel]]).astype(float)
                    mask = np.ones(n, dtype=bool)
                    if invalid in data:
                        mask = data[invalid][sel] == 0
                    if rot is not None:
                        xyz = xyz @ rot.T + trans
                    out: Chunk = {"x": xyz[mask, 0], "y": xyz[mask, 1], "z": xyz[mask, 2]}
                    if "intensity" in data:
                        out["i"] = data["intensity"][sel][mask].astype(np.float32)
                    if {"colorRed", "colorGreen", "colorBlue"} <= data.keys():
                        for k, f in (("r", "colorRed"), ("g", "colorGreen"), ("b", "colorBlue")):
                            v = data[f][sel][mask].astype(np.float64)
                            out[k] = np.clip(v if v.max(initial=0) <= 255 else v / 257.0, 0, 255).astype(np.uint8)
                    yield out
            finally:
                reader.close()
            del imax
    finally:
        e.close()


# ---------------------------------------------------------------------------- LAS/LAZ

def describe_las(path: Path) -> SourceFile:
    import laspy

    try:
        with laspy.open(str(path)) as fh:
            hdr = fh.header
            dims = set(hdr.point_format.dimension_names)
            crs = None
            geo = False
            for v in list(hdr.vlrs) + list(getattr(hdr, "evlrs", None) or []):
                if v.record_id in (2112, 34735):
                    geo = True
                if v.record_id == 2112 and crs is None:
                    try:
                        crs = (v.string if hasattr(v, "string") else bytes(v.record_data).decode("utf-8", "replace"))
                        crs = crs.strip("\x00")[:400]
                    except Exception:  # noqa: BLE001
                        crs = None
            attrs = ["xyz"] + (["intensity"] if "intensity" in dims else []) + \
                (["rgb"] if {"red", "green", "blue"} <= dims else [])
            return SourceFile(
                path=path, kind="las", unit="unidade do CRS do arquivo" if geo else "não declarada (presumida metro)",
                georeferenced=geo, point_count=int(hdr.point_count),
                bounds={"min": [float(v) for v in hdr.mins], "max": [float(v) for v in hdr.maxs]},
                attributes=attrs, crs=crs,
                frame="arquivo LAS/LAZ" + (" (georreferenciado: o arquivo declara CRS)" if geo else " (sem CRS declarado)"),
            )
    except errors.RecapMcpError:
        raise
    except Exception as exc:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, f"LAS/LAZ ilegível: {exc}", path=str(path)) from exc


def _las_chunks(path: Path, chunk: int) -> Iterator[Chunk]:
    import laspy

    with laspy.open(str(path)) as fh:
        dims = set(fh.header.point_format.dimension_names)
        rgb = {"red", "green", "blue"} <= dims
        for pts in fh.chunk_iterator(chunk):
            out: Chunk = {"x": np.asarray(pts.x, dtype=float), "y": np.asarray(pts.y, dtype=float),
                          "z": np.asarray(pts.z, dtype=float)}
            if "intensity" in dims:
                out["i"] = np.asarray(pts.intensity, dtype=np.float32)
            if rgb:
                cols = [np.asarray(getattr(pts, c), dtype=np.float64) for c in ("red", "green", "blue")]
                big = max(c.max(initial=0) for c in cols) > 255
                for k, c in zip(("r", "g", "b"), cols):
                    out[k] = np.clip(c / 257.0 if big else c, 0, 255).astype(np.uint8)
            yield out


# ---------------------------------------------------------------------------- texto

def _text_layout(path: Path) -> tuple[bool, int]:
    """Detecta se há linha de contagem (PTS) e quantas colunas numéricas existem."""
    with path.open("r", encoding="latin-1", errors="replace") as fh:
        head = [ln for ln in itertools.islice(fh, 50) if ln.strip()]
    if not head:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, "Arquivo de texto vazio.", path=str(path))
    first = head[0].replace(",", " ").split()
    has_count = len(first) == 1
    sample = head[1] if has_count and len(head) > 1 else head[0]
    try:
        ncols = len([float(v) for v in sample.replace(",", " ").split()])
    except ValueError as exc:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, "Arquivo de texto sem colunas numéricas.",
                                   path=str(path)) from exc
    if ncols < 3:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, "São necessárias ao menos 3 colunas (X Y Z).",
                                   path=str(path))
    return has_count, ncols


def describe_text(path: Path, input_unit: str = "m") -> SourceFile:
    has_count, ncols = _text_layout(path)
    count = None
    if has_count:
        with path.open("r", encoding="latin-1") as fh:
            try:
                count = int(float(fh.readline().strip()))
            except ValueError:
                count = None
    attrs = ["xyz"] + (["intensity"] if ncols in (4, 7) else []) + (["rgb"] if ncols in (6, 7) else [])
    return SourceFile(
        path=path, kind="text", unit="m (convertido de " + input_unit + ")" if input_unit != "m" else "m (informado)",
        georeferenced=None, point_count=count, bounds=None, attributes=attrs, scale=UNIT_FACTORS[input_unit],
        frame="arquivo de texto (coordenadas como gravadas)",
        notes=[f"Colunas detectadas: {ncols}. Arquivos de texto não declaram unidade; usada '{input_unit}'."],
    )


def _text_chunks(path: Path, chunk: int, scale: float) -> Iterator[Chunk]:
    has_count, ncols = _text_layout(path)
    with path.open("r", encoding="latin-1", errors="replace") as fh:
        if has_count:
            fh.readline()
        while True:
            lines = [ln.replace(",", " ") for ln in itertools.islice(fh, chunk)]
            if not lines:
                break
            arr = np.loadtxt(lines, dtype=np.float64, ndmin=2, usecols=range(ncols))
            out: Chunk = {"x": arr[:, 0] * scale, "y": arr[:, 1] * scale, "z": arr[:, 2] * scale}
            if ncols in (4, 7):
                out["i"] = arr[:, 3].astype(np.float32)
            if ncols in (6, 7):
                c0 = 4 if ncols == 7 else 3
                for k, j in zip(("r", "g", "b"), range(c0, c0 + 3)):
                    out[k] = np.clip(arr[:, j], 0, 255).astype(np.uint8)
            yield out


# ---------------------------------------------------------------------------- resolução

def describe(path: Path, input_unit: str = "m") -> SourceFile:
    ext = path.suffix.lower()
    if ext == ".e57":
        return describe_e57(path)
    if ext in (".las", ".laz"):
        return describe_las(path)
    if ext in TEXT_EXTS:
        return describe_text(path, input_unit)
    raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT, f"Formato {ext} não suportado para leitura de pontos.",
                               supported=sorted(SOURCE_EXTS))


def iter_chunks(src: SourceFile, chunk: int = CHUNK_POINTS) -> Iterator[Chunk]:
    if src.kind == "e57":
        yield from _e57_chunks(src.path, chunk)
    elif src.kind == "las":
        yield from _las_chunks(src.path, chunk)
    else:
        yield from _text_chunks(src.path, chunk, src.scale)


def resolve_sources(source: str, input_unit: str = "m") -> tuple[list[SourceFile], dict[str, Any]]:
    """Aceita um arquivo de origem ou um ``.rcp``; devolve os arquivos e o contexto do projeto."""
    if input_unit not in UNIT_FACTORS:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"input_unit inválida: {input_unit}",
                                   allowed=list(UNIT_FACTORS))
    p = existing_file(source, "source")
    ext = p.suffix.lower()
    context: dict[str, Any] = {"source": str(p), "via_project": ext == ".rcp"}
    if ext == ".rcs":
        raise errors.RecapMcpError(
            errors.UNSUPPORTED_FORMAT,
            "Os pontos de um .rcs não podem ser lidos sem o Reality Solutions SDK. Informe o .rcp (para usar os "
            "arquivos de origem) ou o próprio arquivo de origem (.e57, .las, .laz, .pts, .xyz).")
    if ext != ".rcp":
        return [describe(p, input_unit)], context

    from ..projects import list_scans, read_project

    proj = read_project(str(p))
    scans = list_scans(str(p), verify_rcs_headers=False)["scans"]
    files: dict[str, Path] = {}
    missing: list[str] = []
    for s in scans:
        src = s.get("source_scan") or {}
        raw = src.get("path")
        if not raw:
            missing.append(f"{s.get('name')}: sem rawScanPath")
            continue
        rp = Path(raw)
        if rp.is_file():
            files[os.path.normcase(str(rp))] = rp
        else:
            missing.append(f"{s.get('name')}: {raw}")
    if missing:
        raise errors.RecapMcpError(
            errors.PATH_NOT_FOUND,
            "Arquivos de origem do projeto não encontrados. Sem o Reality Solutions SDK, os pontos só podem ser "
            "lidos dos arquivos de origem.", missing=missing[:50])
    srcs = [describe(fp, input_unit) for fp in files.values()]
    unsupported = [str(s.path) for s in srcs if s.kind not in ("e57", "las", "text")]
    if unsupported:
        raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT, "Origem em formato não suportado.", files=unsupported)
    context.update(project_bounds=proj.get("project_bounds"), project_coordinate_system=proj.get("coordinate_system"),
                   project_scan_count=proj.get("scan_count"), project_total_points=proj.get("total_points"))
    return srcs, context
