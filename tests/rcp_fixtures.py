"""Geradores de fixtures sintéticas de .rcp/.rcs.

A estrutura reproduz a de projetos gerados pelo DeCap 27.0.1.242 (campos e
hierarquia), com caminhos e identificadores fictícios. Nenhuma nuvem real é usada.
"""

from __future__ import annotations

import struct
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr


@dataclass
class ScanSpec:
    name: str
    num_points: int = 1000
    manifest_path: str | None = None  # Path gravado no manifesto
    relative_path: str = ""
    raw_scan_path: str = "C:/dados/entrada.pts"
    translation: tuple[float, float, float] = (0.5, 0.25, 0.0)
    guid: str = field(default_factory=lambda: "{" + str(uuid.uuid4()).replace("-", "_") + "}")
    shot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    shot_type: str = "UNSTRUCTURED"


def manifest_xml(scans: list[ScanSpec], coordinate_system: str = "", major: str = "1",
                 bounds_max: tuple[float, float, float] = (1.0, 0.5, 0.0)) -> str:
    nodes = []
    shots = []
    groups = []
    for s in scans:
        tx, ty, tz = s.translation
        nodes.append(f"""<VoxelTreeRunTime Id="{s.guid}">
<Translation x="{tx}" y="{ty}" z="{tz}" />
<Rotation x="0" y="0" z="0" />
<Scale x="1" y="1" z="1" />
<Visible Value="1" />
<Registered Value="0" />
<NodeName Value={quoteattr(s.name)} />
<Group Value="" />
<Path Value={quoteattr(s.manifest_path or "")} />
<RelativePath Value={quoteattr(s.relative_path)} />
<NumPoints Value="{s.num_points}" />
<HasRGB Value="1" />
<HasNormals Value="1" />
<HasIntensity Value="1" />
</VoxelTreeRunTime>""")
        shots.append(f"""<ShotInfo name={quoteattr(s.name)} id="{s.shot_id}" PCPid="{s.guid}" path={quoteattr(s.manifest_path or "")} relativePath="" rawScanPath={quoteattr(s.raw_scan_path)} type="{s.shot_type}">
<tform><T x="{tx}" y="{ty}" z="{tz}" /></tform>
</ShotInfo>""")
        groups.append(f"""<GroupInfo id="{s.shot_id}"><LaserRegInfo isReg="1" level="FINE_ALIGNED" status="OK" /></GroupInfo>""")
    mx, my, mz = bounds_max
    bounds_attrs = " ".join(f'{k}="{v}"' for k, v in (("maxx", mx), ("maxy", my), ("maxz", mz)) if v != 0)
    return f"""<Autodesk Version="1.0">
<ProjectId UUID="11111111-2222-3333-4444-555555555555" />
<Nodes>
{''.join(nodes)}
</Nodes>
<Measurements />
<PointCloudSegmentList><RegionManager><RegionArray /></RegionManager></PointCloudSegmentList>
<ViewStates />
<LimitBoxes><LimitBox Visible="0" NodeName="Box" FirstTime="0" /></LimitBoxes>
<GeoReference><Origin X="0" Y="0" Z="0" /></GeoReference>
<UnitType Value="1" IsSurveyMode="0" />
<GlobalTransformation m11="1" m21="0" m31="0" m41="0" m12="0" m22="1" m32="0" m42="0" m13="0" m23="0" m33="1" m43="0" m14="0" m24="0" m34="0" m44="1" />
<CoordinateSystem Value={quoteattr(coordinate_system)} />
<ProjectMetaData MajorVersion="{major}" MinorVersion="6" PatchVersion="0">
<SaveInformation Application="decap" ApplicationVersion="27.0.1.242" Timestamp="20260101T000000" UserId="usuario" Count="0" />
</ProjectMetaData>
<Project ver="1.0.0" app="Autodesk ReCap" path="">
{''.join(shots)}
<GroupInfo name="Default Group" id="g0"><LaserRegInfo isReg="0" />{''.join(groups)}</GroupInfo>
<map id="m0" name="Default Map"><bounds {bounds_attrs} /></map>
</Project>
</Autodesk>
"""


def write_rcp(path: Path, xml: str, extra: dict[str, bytes] | None = None, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{uuid.uuid4()}.xml", xml.encode(encoding))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def rcs_header(num_points: int, guid: str, translation=(0.5, 0.25, 0.0), half=(0.5, 0.25, 0.0)) -> bytes:
    buf = bytearray(0x140)
    buf[0:5] = b"ADOCT"
    struct.pack_into("<II", buf, 8, 3, 2)
    d = [0.0] * 24
    d[0:3] = translation
    d[6:9] = (1.0, 1.0, 1.0)
    d[18:21] = (-half[0], -half[1], -half[2])
    d[21:24] = half
    struct.pack_into("<24d", buf, 0x10, *d)
    struct.pack_into("<4I", buf, 0xD0, 0x83, 2, num_points, 0)
    g = guid.encode("ascii")
    buf[0xE3:0xE3 + len(g)] = g
    return bytes(buf) + b"\x00" * 64


def write_rcs(path: Path, num_points: int, guid: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rcs_header(num_points, guid))
    return path


def make_project(folder: Path, name: str, scans: list[ScanSpec], create_rcs: bool | list[bool] = True,
                 coordinate_system: str = "") -> Path:
    """Cria ``<folder>/<name>.rcp`` e ``<name> Support/<scan>.rcs`` como o DeCap faz."""
    support = folder / f"{name} Support"
    flags = create_rcs if isinstance(create_rcs, list) else [create_rcs] * len(scans)
    for s, flag in zip(scans, flags):
        if s.manifest_path is None:
            s.manifest_path = (support / f"{s.name}.rcs").as_posix()
        if flag:
            write_rcs(support / f"{s.name}.rcs", s.num_points, s.guid)
    return write_rcp(folder / f"{name}.rcp", manifest_xml(scans, coordinate_system))
