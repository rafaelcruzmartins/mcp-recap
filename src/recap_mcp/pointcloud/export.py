"""Exportação de um subconjunto da nuvem para .pts, .xyz ou .e57 (arquivo novo, nunca sobrescreve)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .. import errors
from ..paths import as_absolute
from .index import CloudIndex, Region

FORMATS = ("pts", "xyz", "e57")
MAX_E57_POINTS = int(os.environ.get("RECAP_MCP_MAX_E57_EXPORT_POINTS", "50000000"))
NAME_CHARS = set('\\/:*?"<>|')


def plan_export(output_folder: str, file_name: str, fmt: str) -> Path:
    if fmt not in FORMATS:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"format deve ser um de {FORMATS}.")
    if not file_name or any(c in NAME_CHARS for c in file_name) or len(file_name) > 120:
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "file_name inválido (sem \\ / : * ? \" < > |).")
    out = as_absolute(output_folder, "output_folder")
    if out.exists() and not out.is_dir():
        raise errors.RecapMcpError(errors.NOT_A_DIRECTORY, f"Não é uma pasta: {out}")
    if not out.exists() and not out.parent.is_dir():
        raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"A pasta-mãe do destino não existe: {out.parent}")
    stem = file_name[: -len(fmt) - 1] if file_name.lower().endswith("." + fmt) else file_name
    target = out / f"{stem}.{fmt}"
    if target.exists():
        raise errors.RecapMcpError(errors.DESTINATION_EXISTS, f"Já existe '{target}'; o servidor não sobrescreve.",
                                   path=str(target))
    return target


def run_export(cloud: CloudIndex, region: Region, target: Path, fmt: str,
               progress: Callable[[float, str], None], cancelled: Callable[[], bool],
               log: Callable[[str], None]) -> dict[str, Any]:
    target.parent.mkdir(parents=False, exist_ok=True)
    tmp = target.with_name(target.name + ".parcial")
    has_i, has_rgb = cloud.m["has_intensity"], cloud.m["has_rgb"]
    count = 0
    try:
        if fmt in ("pts", "xyz"):
            with open(tmp, "w", encoding="ascii", newline="\n") as fh:
                if fmt == "pts":
                    fh.write(" " * 20 + "\n")  # reservado para a contagem
                for pts, frac in cloud.iter_region(region, cancelled):
                    n = len(pts["x"])
                    if n:
                        cols = [pts["x"], pts["y"], pts["z"]]
                        fmts = ["%.4f"] * 3
                        if fmt == "pts" or has_i:  # PTS sempre tem a coluna de intensidade
                            cols.append(pts["i"])
                            fmts.append("%.0f" if fmt == "pts" else "%.3f")
                        if has_rgb:
                            cols += [pts["r"], pts["g"], pts["b"]]
                            fmts += ["%d"] * 3
                        np.savetxt(fh, np.column_stack(cols), fmt=" ".join(fmts))
                        count += n
                    progress(99 * frac, f"{count:,} pontos gravados")
            if fmt == "pts":
                with open(tmp, "r+b") as fh:
                    fh.write(str(count).ljust(20).encode("ascii"))
        else:
            parts: dict[str, list[np.ndarray]] = {k: [] for k in ("x", "y", "z", "i", "r", "g", "b")}
            for pts, frac in cloud.iter_region(region, cancelled):
                count += len(pts["x"])
                if count > MAX_E57_POINTS:
                    raise errors.RecapMcpError(
                        errors.LIMIT_EXCEEDED, f"Exportação E57 limitada a {MAX_E57_POINTS:,} pontos (é montada em "
                        "memória); use .pts ou reduza a região.")
                for k, lst in parts.items():
                    lst.append(pts[k])
                progress(80 * frac, f"{count:,} pontos lidos")
            import pye57

            data = {"cartesianX": np.concatenate(parts["x"]), "cartesianY": np.concatenate(parts["y"]),
                    "cartesianZ": np.concatenate(parts["z"])}
            if has_i:
                data["intensity"] = np.concatenate(parts["i"]).astype(np.float32)
            if has_rgb:
                for k, f in (("r", "colorRed"), ("g", "colorGreen"), ("b", "colorBlue")):
                    data[f] = np.concatenate(parts[k]).astype(np.uint8)
            e = pye57.E57(str(tmp), mode="w")
            e.write_scan_raw(data, name=target.stem)
            e.close()
        if cancelled():
            raise_cancel()
        if target.exists():
            raise errors.RecapMcpError(errors.DESTINATION_EXISTS, f"'{target}' foi criado por outro processo.")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log(f"exportados {count} pontos para {target}")
    return {"output": str(target), "format": fmt, "points": count, "size_bytes": target.stat().st_size,
            "attributes": ["xyz"] + (["intensity"] if has_i else []) + (["rgb"] if has_rgb else [])}


def raise_cancel() -> None:
    from .index import Cancelled

    raise Cancelled()
