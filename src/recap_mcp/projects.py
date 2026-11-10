"""Busca e leitura de metadados de projetos ReCap (.rcp) e scans (.rcs).

O que é lido, e com que grau de confiança:

* ``.rcp`` é um ZIP com um manifesto XML. O manifesto é lido **em memória**,
  sem extração para disco, com limites de tamanho, número de entradas e taxa
  de compressão. Só são devolvidos campos cuja estrutura foi observada em
  projetos gerados pelo DeCap 27.0.1.242; o formato não é documentado pela
  Autodesk.
* ``.rcs`` é um octree binário proprietário. Apenas o cabeçalho é lido (magic,
  versão, número de pontos, identificador e limites locais). **Pontos não são
  lidos nem enumerados.** Leitura completa exige o Reality Solutions SDK.
"""

from __future__ import annotations

import os
import re
import struct
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import errors
from .paths import existing_dir, existing_file

# Limites de segurança para leitura do manifesto
MAX_RCP_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_SCANS_RETURNED = 5000

RCS_MAGIC = b"ADOCT"
RCS_HEADER_BYTES = 0x140
LOSSY_CHAR = "�"

SCHEMA_NOTE = (
    "O formato RCP/RCS não é documentado publicamente pela Autodesk. Os campos devolvidos foram "
    "conferidos em projetos gerados pelo DeCap 27.0.1.242; projetos de outras versões podem ter "
    "campos ausentes, que aparecem como null."
)


# ---------------------------------------------------------------------------
# find_projects
# ---------------------------------------------------------------------------

def find_projects(
    folder: str,
    include_rcs: bool = True,
    max_depth: int = 6,
    max_results: int = 200,
    max_dirs: int = 20000,
) -> dict[str, Any]:
    root = existing_dir(folder, "folder")
    if not (0 <= max_depth <= 32):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "max_depth deve estar entre 0 e 32.")
    if not (1 <= max_results <= 5000):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, "max_results deve estar entre 1 e 5000.")
    exts = {".rcp"} | ({".rcs"} if include_rcs else set())

    results: list[dict[str, Any]] = []
    access_errors: list[str] = []
    dirs_visited = 0
    truncated_reason: str | None = None
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        current, depth = stack.pop()
        dirs_visited += 1
        if dirs_visited > max_dirs:
            truncated_reason = f"limite de {max_dirs} pastas visitadas"
            break
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError as exc:
            if len(access_errors) < 20:
                access_errors.append(f"{current}: {exc.strerror or exc}")
            continue
        subdirs = []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue  # não segue links nem junções
                if entry.is_dir(follow_symlinks=False):
                    if depth < max_depth:
                        subdirs.append(Path(entry.path))
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in exts:
                    st = entry.stat(follow_symlinks=False)
                    results.append({
                        "path": entry.path,
                        "type": ext[1:],
                        "size_bytes": st.st_size,
                        "modified": _iso(st.st_mtime),
                    })
                    if len(results) >= max_results:
                        truncated_reason = f"limite de {max_results} resultados"
                        break
            except OSError as exc:
                if len(access_errors) < 20:
                    access_errors.append(f"{entry.path}: {exc.strerror or exc}")
        if truncated_reason:
            break
        stack.extend(reversed([(d, depth + 1) for d in subdirs]))

    return {
        "ok": True,
        "folder": str(root),
        "count": len(results),
        "truncated": truncated_reason is not None,
        "truncated_reason": truncated_reason,
        "directories_visited": dirs_visited,
        "access_errors": access_errors,
        "results": results,
    }


def _iso(ts: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Manifesto RCP
# ---------------------------------------------------------------------------

def load_manifest(rcp: Path) -> tuple[ET.Element, dict[str, Any]]:
    """Abre o ZIP e devolve a raiz XML do manifesto e informações da leitura."""
    size = rcp.stat().st_size
    if size > MAX_RCP_BYTES:
        raise errors.RecapMcpError(errors.LIMIT_EXCEEDED, f"Arquivo .rcp maior que {MAX_RCP_BYTES} bytes.",
                                   path=str(rcp), size_bytes=size)
    if not zipfile.is_zipfile(rcp):
        with rcp.open("rb") as fh:
            head = fh.read(8)
        hint = " Parece um .rcs (octree de pontos); use a mesma ferramenta com o arquivo .rcs." \
            if head.startswith(RCS_MAGIC) else ""
        raise errors.RecapMcpError(errors.INVALID_PROJECT,
                                   f"'{rcp.name}' não é um projeto .rcp válido (não é ZIP).{hint}",
                                   path=str(rcp))
    try:
        zf = zipfile.ZipFile(rcp)
    except (zipfile.BadZipFile, OSError) as exc:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, f"ZIP corrompido: {exc}", path=str(rcp)) from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise errors.RecapMcpError(errors.LIMIT_EXCEEDED,
                                       f"O .rcp tem {len(infos)} entradas (limite {MAX_ZIP_ENTRIES}).",
                                       path=str(rcp))
        candidates = [i for i in infos
                      if i.filename.lower().endswith(".xml") and "/" not in i.filename
                      and "\\" not in i.filename and not i.is_dir()]
        if not candidates:
            raise errors.RecapMcpError(
                errors.INVALID_PROJECT,
                "O .rcp é um ZIP, mas não contém manifesto XML na raiz.",
                path=str(rcp), entries=[i.filename for i in infos[:20]],
            )
        last_exc: Exception | None = None
        for info in candidates:
            if info.file_size > MAX_MANIFEST_BYTES:
                raise errors.RecapMcpError(errors.LIMIT_EXCEEDED,
                                           f"Manifesto declarado com {info.file_size} bytes "
                                           f"(limite {MAX_MANIFEST_BYTES}).", path=str(rcp))
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO \
                    and info.file_size > 1024 * 1024:
                raise errors.RecapMcpError(errors.LIMIT_EXCEEDED,
                                           "Taxa de compressão suspeita no manifesto.", path=str(rcp))
            raw = _read_limited(zf, info)
            root, encoding = _parse_xml(raw)
            if root is None:
                last_exc = encoding  # type: ignore[assignment]
                continue
            if root.tag != "Autodesk":
                continue
            return root, {
                "manifest_entry": info.filename,
                "manifest_bytes": len(raw),
                "manifest_encoding": encoding,
                "zip_entries": len(infos),
            }
        if last_exc is not None:
            raise errors.RecapMcpError(errors.INVALID_PROJECT, f"Manifesto XML inválido: {last_exc}",
                                       path=str(rcp))
        raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT,
                                   "Manifesto XML com raiz diferente de <Autodesk>; formato não suportado.",
                                   path=str(rcp))


def _read_limited(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    chunks = []
    total = 0
    with zf.open(info) as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_MANIFEST_BYTES:
                raise errors.RecapMcpError(errors.LIMIT_EXCEEDED, "Manifesto excede o limite ao descompactar.")
            chunks.append(block)
    return b"".join(chunks)


def _parse_xml(raw: bytes) -> tuple[ET.Element | None, Any]:
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in raw.lower():
        return None, ValueError("DTD/entidades não são aceitos no manifesto")
    for enc in ("utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        try:
            return ET.fromstring(text), enc
        except ET.ParseError as exc:
            return None, exc
    return None, ValueError("codificação desconhecida")


def _attr(el: ET.Element | None, name: str, default: str | None = None) -> str | None:
    if el is None:
        return default
    return el.attrib.get(name, default)


def _value(root: ET.Element, path: str) -> str | None:
    return _attr(root.find(path), "Value")


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str | None) -> int | None:
    v = _num(text)
    return int(v) if v is not None else None


def _bool(text: str | None) -> bool | None:
    if text is None:
        return None
    return text.strip() not in ("0", "", "false", "False")


def _xyz(el: ET.Element | None, upper: bool = False) -> list[float] | None:
    if el is None:
        return None
    keys = ("X", "Y", "Z") if upper else ("x", "y", "z")
    vals = [_num(el.attrib.get(k)) for k in keys]
    if all(v is None for v in vals):
        return None
    return [v if v is not None else 0.0 for v in vals]


def _map_bounds(el: ET.Element | None) -> dict[str, Any] | None:
    if el is None:
        return None
    b = el.find("bounds")
    if b is None:
        return None
    keys = ("minx", "miny", "minz", "maxx", "maxy", "maxz")
    vals = {k: _num(b.attrib.get(k)) for k in keys}
    filled = {k: (v if v is not None else 0.0) for k, v in vals.items()}
    mn = [filled["minx"], filled["miny"], filled["minz"]]
    mx = [filled["maxx"], filled["maxy"], filled["maxz"]]
    if any(mx[i] < mn[i] for i in range(3)):
        return None  # caixa vazia (o ReCap grava min > max)
    return {
        "min": mn,
        "max": mx,
        "size": [round(mx[i] - mn[i], 9) for i in range(3)],
        "omitted_attributes_as_zero": [k for k, v in vals.items() if v is None],
    }


def _child_count(el: ET.Element | None) -> int:
    return 0 if el is None else len(el)


def _support_dir(rcp: Path) -> Path:
    return rcp.with_name(f"{rcp.stem} Support")


def read_project(path: str) -> dict[str, Any]:
    p = existing_file(path, "path")
    ext = p.suffix.lower()
    if ext == ".rcs":
        return {"ok": True, "type": "rcs", **read_rcs_header(p)}
    if ext != ".rcp":
        raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT,
                                   f"Extensão '{p.suffix}' não suportada; use .rcp ou .rcs.", path=str(p))
    root, info = load_manifest(p)
    warnings: list[str] = []

    meta = root.find("ProjectMetaData")
    schema_version = None
    if meta is not None:
        schema_version = ".".join(meta.attrib.get(k, "?") for k in ("MajorVersion", "MinorVersion", "PatchVersion"))
        if meta.attrib.get("MajorVersion") not in (None, "1"):
            warnings.append(f"Versão de esquema {schema_version} não testada; campos podem estar ausentes.")
    saves = [
        {"application": s.attrib.get("Application"), "application_version": s.attrib.get("ApplicationVersion"),
         "timestamp": s.attrib.get("Timestamp")}
        for s in (meta.findall("SaveInformation") if meta is not None else [])
    ]

    nodes = root.findall("./Nodes/VoxelTreeRunTime")
    total_points = 0
    known_points = True
    for n in nodes:
        v = _int(_value(n, "NumPoints"))
        if v is None:
            known_points = False
        else:
            total_points += v

    proj = root.find("Project")
    default_map = None
    if proj is not None:
        for mp in proj.findall("map"):
            if mp.attrib.get("name") == "Default Map":
                default_map = mp
    gt = root.find("GlobalTransformation")
    global_tf = None
    if gt is not None:
        global_tf = [[_num(gt.attrib.get(f"m{r}{c}")) for c in (1, 2, 3, 4)] for r in (1, 2, 3, 4)]

    unit_el = root.find("UnitType")
    support = _support_dir(p)
    if not nodes:
        warnings.append("O projeto não contém scans (<Nodes> vazio).")

    return {
        "ok": True,
        "type": "rcp",
        "path": str(p),
        "size_bytes": p.stat().st_size,
        "project_id": _attr(root.find("ProjectId"), "UUID"),
        "manifest": info,
        "schema_version": schema_version,
        "save_history": saves,
        "scan_count": len(nodes),
        "total_points": total_points if known_points else None,
        "coordinate_system": _value(root, "CoordinateSystem") or None,
        "geo_reference_origin": _xyz(root.find("./GeoReference/Origin"), upper=True),
        "global_transformation": global_tf,
        "project_bounds": _map_bounds(default_map),
        "display_unit": {
            "unit_type_raw": _int(_attr(unit_el, "Value")),
            "survey_mode": _bool(_attr(unit_el, "IsSurveyMode")),
            "note": ("Código bruto da unidade de exibição. A Autodesk não publica a tabela; nos testes, "
                     "projetos importados em mm e em m gravaram o mesmo valor, portanto ele não indica "
                     "a unidade de entrada."),
        },
        "counts": {
            "measurements": _child_count(root.find("Measurements")),
            "regions": len(root.findall("./PointCloudSegmentList/RegionManager/RegionArray/Layer")),
            "limit_boxes": len(root.findall("./LimitBoxes/LimitBox")),
            "view_states": len(root.findall("./ViewStates/ViewState")),
        },
        "support_folder": {"path": str(support), "exists": support.is_dir()},
        "warnings": warnings,
        "note": SCHEMA_NOTE,
    }


# ---------------------------------------------------------------------------
# list_scans
# ---------------------------------------------------------------------------

def _resolve_rcs(rcp: Path, manifest_path: str | None, relative: str | None) -> dict[str, Any]:
    """Resolve o .rcs de um scan sem confiar cegamente no caminho absoluto gravado.

    Ordem: RelativePath (relativo ao .rcp) → Path absoluto → ``<projeto> Support\\<arquivo>``.
    ``Path`` é absoluto e deixa de valer quando o projeto é movido ou copiado para outra
    pasta ou máquina; nesse caso a pasta Support ao lado do .rcp é usada. Se o caminho
    gravado contiver U+FFFD (texto corrompido por outra ferramenta), ele não é usado.
    """
    lossy = bool(manifest_path and LOSSY_CHAR in manifest_path)
    tried: list[str] = []
    if relative:
        cand = (rcp.parent / relative.replace("\\", os.sep).lstrip("./\\")).resolve()
        tried.append(str(cand))
        if cand.is_file():
            return {"resolved_path": str(cand), "resolved_by": "RelativePath", "exists": True,
                    "manifest_path_lossy": lossy}
    if manifest_path and not lossy:
        cand = Path(manifest_path.replace("/", os.sep))
        tried.append(str(cand))
        if cand.is_file():
            return {"resolved_path": str(cand), "resolved_by": "Path", "exists": True,
                    "manifest_path_lossy": lossy}
    if manifest_path:
        name = re.split(r"[\\/]", manifest_path)[-1]
        if name and LOSSY_CHAR not in name:
            cand = _support_dir(rcp) / name
            tried.append(str(cand))
            if cand.is_file():
                return {"resolved_path": str(cand), "resolved_by": "pasta Support", "exists": True,
                        "manifest_path_lossy": lossy}
    return {"resolved_path": None, "resolved_by": None, "exists": False, "manifest_path_lossy": lossy,
            "tried": tried}


def list_scans(path: str, verify_rcs_headers: bool = True, max_scans: int = 1000) -> dict[str, Any]:
    p = existing_file(path, "path")
    if p.suffix.lower() != ".rcp":
        raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT, "list_scans aceita somente arquivos .rcp.",
                                   path=str(p))
    if not (1 <= max_scans <= MAX_SCANS_RETURNED):
        raise errors.RecapMcpError(errors.INVALID_ARGUMENT, f"max_scans deve estar entre 1 e {MAX_SCANS_RETURNED}.")
    root, _ = load_manifest(p)

    shots: dict[str, ET.Element] = {}
    for shot in root.findall("./Project/ShotInfo"):
        pcp = shot.attrib.get("PCPid")
        if pcp:
            shots[pcp] = shot
    reg_by_shot: dict[str, ET.Element] = {}
    for grp in root.iter("GroupInfo"):
        lri = grp.find("LaserRegInfo")
        if grp.attrib.get("id") and lri is not None:
            reg_by_shot[grp.attrib["id"]] = lri

    nodes = root.findall("./Nodes/VoxelTreeRunTime")
    scans: list[dict[str, Any]] = []
    missing = 0
    for node in nodes[:max_scans]:
        sid = node.attrib.get("Id")
        manifest_path = _value(node, "Path")
        relative = _value(node, "RelativePath") or None
        rcs = _resolve_rcs(p, manifest_path, relative)
        rcs["manifest_path"] = manifest_path
        rcs["relative_path"] = relative
        num_points = _int(_value(node, "NumPoints"))
        if rcs["exists"]:
            rcs["size_bytes"] = Path(rcs["resolved_path"]).stat().st_size
            if verify_rcs_headers:
                try:
                    hdr = read_rcs_header(Path(rcs["resolved_path"]))
                    rcs["header_check"] = {
                        "num_points": hdr["num_points"],
                        "matches_manifest_points": hdr["num_points"] == num_points,
                        "matches_manifest_id": (hdr["guid"] == sid) if hdr["guid"] else None,
                    }
                except errors.RecapMcpError as exc:
                    rcs["header_check"] = {"error": exc.message}
        else:
            missing += 1

        shot = shots.get(sid or "")
        source = None
        shot_type = None
        registration = None
        if shot is not None:
            shot_type = shot.attrib.get("type")
            raw = shot.attrib.get("rawScanPath")
            if raw:
                lossy = LOSSY_CHAR in raw
                source = {"path": raw, "path_lossy": lossy,
                          "exists": None if lossy else Path(raw).is_file()}
            lri = reg_by_shot.get(shot.attrib.get("id", ""))
            if lri is not None:
                registration = {"is_registered": _bool(lri.attrib.get("isReg")),
                                "level": lri.attrib.get("level"), "status": lri.attrib.get("status")}

        scans.append({
            "id": sid,
            "name": _value(node, "NodeName"),
            "group": _value(node, "Group") or None,
            "type": shot_type,
            "num_points": num_points,
            "has_rgb": _bool(_value(node, "HasRGB")),
            "has_intensity": _bool(_value(node, "HasIntensity")),
            "has_normals": _bool(_value(node, "HasNormals")),
            "visible": _bool(_value(node, "Visible")),
            "translation": _xyz(node.find("Translation")),
            "registration": registration,
            "rcs": rcs,
            "source_scan": source,
        })

    return {
        "ok": True,
        "path": str(p),
        "scan_count": len(nodes),
        "returned": len(scans),
        "truncated": len(nodes) > len(scans),
        "missing_rcs_count": missing,
        "scans": scans,
        "note": SCHEMA_NOTE,
    }


# ---------------------------------------------------------------------------
# Cabeçalho RCS
# ---------------------------------------------------------------------------

_GUID_RE = re.compile(rb"\{[0-9A-Fa-f_\-]{36}\}")


def read_rcs_header(path: Path) -> dict[str, Any]:
    """Lê somente os primeiros bytes de um .rcs. Não decodifica pontos."""
    try:
        with path.open("rb") as fh:
            head = fh.read(RCS_HEADER_BYTES)
    except OSError as exc:
        raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"Não foi possível ler {path}: {exc}") from exc
    if not head.startswith(RCS_MAGIC):
        raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT,
                                   f"'{path.name}' não tem a assinatura ADOCT de um .rcs.", path=str(path))
    if len(head) < RCS_HEADER_BYTES:
        raise errors.RecapMcpError(errors.INVALID_PROJECT, f"Cabeçalho .rcs truncado: {path.name}",
                                   path=str(path))
    major, minor = struct.unpack_from("<II", head, 8)
    d = struct.unpack_from("<24d", head, 0x10)
    num_points = struct.unpack_from("<I", head, 0xD8)[0]
    m = _GUID_RE.search(head, 0xD0)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "format_version": f"{major}.{minor}",
        "num_points": num_points,
        "guid": m.group(0).decode("ascii") if m else None,
        "translation": [round(v, 9) for v in d[0:3]],
        "local_data_bounds": {"min": [round(v, 9) for v in d[18:21]], "max": [round(v, 9) for v in d[21:24]]},
        "note": ("Cabeçalho lido por engenharia reversa (assinatura ADOCT). num_points, guid e translation "
                 "conferem com o manifesto .rcp nos testes com DeCap 27.0.1.242. Para ver os pontos, use "
                 "open_cloud."),
    }
