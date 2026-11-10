"""Leitura de pontos pelo Plano B (arquivos de origem), com dados sintéticos."""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from rcp_fixtures import ScanSpec, manifest_xml, write_rcp

from recap_mcp import errors
from recap_mcp.jobs import JobManager
from recap_mcp.pointcloud import analysis, export, index, sources


def grid_pts(path: Path, n: int = 30, step: float = 0.1) -> Path:
    """Mesma grade do projeto 'Teste MCP': 30 x 30 pontos, 2,9 x 2,9 m, z = 0, com intensidade e RGB."""
    rows = [f"{i * step:.2f} {j * step:.2f} 0 50 120 120 120" for i in range(n) for j in range(n)]
    path.write_text(f"{n * n}\n" + "\n".join(rows) + "\n", encoding="ascii")
    return path


@pytest.fixture
def state(tmp_path) -> Path:
    return tmp_path / "estado"


@pytest.fixture
def grid(tmp_path) -> Path:
    d = tmp_path / "entrada ção"
    d.mkdir()
    return grid_pts(d / "grade.pts")


@pytest.fixture
def cloud(state, grid):
    return index.open_cloud(state, str(grid))


def test_info_and_frame(cloud):
    i = analysis.info(cloud)
    assert i["total_points"] == 900
    assert i["bounds"] == {"min": [0.0, 0.0, 0.0], "max": [2.9, 2.9, 0.0]}
    assert i["attributes"] == ["xyz", "intensity", "rgb"]
    assert i["coordinate_frame"]["coordinates"] == "arquivo de origem"


def test_query_box_exact_and_sample_cap(cloud):
    q = analysis.query_box(cloud, [0, 0, -1], [1, 1, 1], 5)
    assert q["points_in_region"] == 121 and q["count_is_exact"]
    assert q["sample"]["size"] == 5 and q["sample"]["columns"] == ["x", "y", "z", "i", "r", "g", "b"]
    full = analysis.query_box(cloud, None, None, 0)
    assert full["points_in_region"] == 900  # pontos sobre os limites máximos não se perdem
    with pytest.raises(errors.RecapMcpError):
        analysis.query_box(cloud, None, None, 10001)
    with pytest.raises(errors.RecapMcpError):
        analysis.query_box(cloud, [1, 1, 1], [0, 0, 0], 10)


def test_histogram_sections_profile_nearest_measure(cloud):
    h = analysis.z_histogram(cloud, bin_size_m=0.1)
    assert sum(b["count"] for b in h["bins"]) == 900 and h["peaks"][0]["z_estimate"] == 0.0
    v = analysis.section(cloud, "vertical", 0.12, p1=[0, 1.0], p2=[2.9, 1.0], sample_size=3)
    assert v["points_in_region"] == 30 and v["points_2d"]["columns"][:2] == ["s", "z"]
    hz = analysis.section(cloud, "horizontal", 0.1, z=0.0, sample_size=0)
    assert hz["points_in_region"] == 900
    img = Image.open(io.BytesIO(hz["png"]))
    assert img.format == "PNG" and img.width > 300
    p = analysis.profile(cloud, [[0, 0], [2.9, 2.9], [0, 2.9]], 0.15, 0.5, 5)
    assert p["polyline_length"] == pytest.approx(2.9 * 2 ** 0.5 + 2.9)
    assert p["points_in_corridor"] == 117
    n = analysis.nearest_points(cloud, [1.04, 1.02, 0.0], 3, 0.2)
    assert n["nearest"]["rows"][0][:3] == [1.0, 1.0, 0.0]
    m = analysis.measure(cloud, [0.01, 0.01, 0], [2.89, 0.02, 0], True, 0.05)
    assert m["distance_3d"] == pytest.approx(2.9) and m["snap"]["p1"]["point"] == [0.0, 0.0, 0.0]
    with pytest.raises(errors.RecapMcpError):
        analysis.section(cloud, "diagonal", 0.1)


def test_render_views(cloud):
    for view in ("top", "front", "right"):
        r = analysis.render_view(cloud, view, None, None, None, "elevation", 400, 2)
        assert Image.open(io.BytesIO(r["png"])).format == "PNG"
        assert r["coordinate_frame"]["coordinates"] == "arquivo de origem"
    r = analysis.render_view(cloud, "custom", [1, 1, -1], None, None, "rgb", 400, 1)
    assert r["view"]["screen_basis"]["u"] == pytest.approx([0.707107, -0.707107, 0.0])
    with pytest.raises(errors.RecapMcpError):
        analysis.render_view(cloud, "custom", [0, 0, 0])


def test_sampling_when_over_budget(cloud, monkeypatch):
    r = cloud.read(analysis.box_region(cloud), max_points=100)
    assert r["sampled"] and r["stride"] == 9 and not r["count_is_exact"]
    assert abs(r["count"] - 900) <= 9


def test_e57_with_pose_is_transformed(tmp_path, state):
    pye57 = pytest.importorskip("pye57")
    from pyquaternion import Quaternion

    world = np.array([[3.0, 2.0, 0.0], [4.0, 2.0, 0.0], [3.0, 3.0, 1.0]])
    q = Quaternion(axis=[0, 0, 1], degrees=90)
    t = np.array([3.0, 2.0, 0.0])
    local = (world - t) @ q.rotation_matrix
    f = tmp_path / "pose.e57"
    e = pye57.E57(str(f), mode="w")
    e.write_scan_raw({"cartesianX": local[:, 0], "cartesianY": local[:, 1], "cartesianZ": local[:, 2]},
                     name="s", rotation=q.elements, translation=t)
    e.close()
    src = sources.describe(f)
    assert src.point_count == 3 and src.scans[0]["pose_scan_to_file"]["translation"] == [3.0, 2.0, 0.0]
    c = index.open_cloud(state, str(f))
    mn, mx = c.bounds
    assert mn == pytest.approx([3, 2, 0], abs=1e-6) and mx == pytest.approx([4, 3, 1], abs=1e-6)


def test_las_source(tmp_path, state):
    laspy = pytest.importorskip("laspy")
    h = laspy.LasHeader(point_format=3, version="1.2")
    h.scales = [0.001] * 3
    h.offsets = [500000.0, 7400000.0, 0.0]
    las = laspy.LasData(h)
    las.x = np.array([500000.0, 500001.0])
    las.y = np.array([7400000.0, 7400002.0])
    las.z = np.array([700.0, 701.0])
    las.red, las.green, las.blue = (np.array(v, dtype=np.uint16) for v in ([65535, 0], [0, 65535], [0, 0]))
    f = tmp_path / "t.laz"
    las.write(str(f))
    c = index.open_cloud(state, str(f))
    q = analysis.query_box(c, None, None, 10)
    assert q["points_in_region"] == 2
    assert q["sample"]["rows"][0][-3:] == [255, 0, 0]  # RGB de 16 bits convertido para 8 bits
    assert q["coordinate_frame"]["georeferenced"] is False


def test_export_roundtrip_and_no_overwrite(cloud, tmp_path):
    reg = analysis.box_region(cloud, [0, 0, -1], [0.5, 0.5, 1])
    for fmt in ("pts", "xyz"):
        target = export.plan_export(str(tmp_path / "saída"), "recorte", fmt)
        r = export.run_export(cloud, reg, target, fmt, lambda p, m: None, lambda: False, lambda s: None)
        assert r["points"] == 36
        back = sum(len(ch["x"]) for ch in sources.iter_chunks(sources.describe(target)))
        assert back == 36
        with pytest.raises(errors.RecapMcpError) as ei:
            export.plan_export(str(tmp_path / "saída"), "recorte", fmt)
        assert ei.value.code == errors.DESTINATION_EXISTS
    target = export.plan_export(str(tmp_path / "saída"), "cancelado", "pts")
    with pytest.raises(index.Cancelled):
        export.run_export(cloud, reg, target, "pts", lambda p, m: None, lambda: True, lambda s: None)
    assert not target.exists() and not list((tmp_path / "saída").glob("*.parcial"))


def test_large_cloud_requires_index_job(state, grid, monkeypatch):
    monkeypatch.setattr(index, "SYNC_INDEX_MAX_POINTS", 10)
    with pytest.raises(errors.RecapMcpError) as ei:
        index.open_cloud(state, str(grid))
    assert ei.value.code == index.CACHE_REQUIRED


def test_index_as_job_with_progress_and_cancel(state, grid, tmp_path):
    srcs, ctx = sources.resolve_sources(str(grid))
    m = JobManager(tmp_path / "jobs", lambda: ["nao-usado"])
    job = m.start_task("index_point_cloud", {"source": str(grid)},
                       lambda progress, cancel, log: {"folder": str(index.build_index(state, srcs, ctx, "m", progress))})
    st = m.wait(job["job_id"], 60)
    assert st["status"] == "succeeded" and st["progress"]["percent"] == 100.0
    gate = threading.Event()

    def slow(progress, cancel, log):
        gate.set()
        cancel.wait(30)
        return index.build_index(state, srcs, ctx, "mm", progress, cancelled=cancel.is_set)

    job2 = m.start_task("index_point_cloud", {"source": str(grid)}, slow)
    gate.wait(10)
    res = m.cancel(job2["job_id"])
    assert res["status"] == "cancelled"
    assert not [p for p in (Path(state) / "cache").iterdir() if ".tmp-" in p.name]


def test_source_resolution_errors(tmp_path):
    rcs = tmp_path / "a.rcs"
    rcs.write_bytes(b"ADOCT" + b"\0" * 400)
    with pytest.raises(errors.RecapMcpError) as ei:
        sources.resolve_sources(str(rcs))
    assert ei.value.code == errors.UNSUPPORTED_FORMAT and "SDK" in ei.value.message
    rcp = write_rcp(tmp_path / "p.rcp", manifest_xml([ScanSpec("s", raw_scan_path=str(tmp_path / "sumiu.e57"))]))
    with pytest.raises(errors.RecapMcpError) as ei:
        sources.resolve_sources(str(rcp))
    assert ei.value.code == errors.PATH_NOT_FOUND and ei.value.details["missing"]
    with pytest.raises(errors.RecapMcpError):
        sources.resolve_sources(str(rcp), input_unit="km")


def test_rcp_uses_raw_scan_and_compares_bounds(tmp_path, state, grid):
    rcp = write_rcp(tmp_path / "proj.rcp", manifest_xml([ScanSpec("grade", raw_scan_path=str(grid))],
                                                        bounds_max=(2.9, 2.9, 0.0)))
    c = index.open_cloud(state, str(rcp))
    fr = analysis.frame_info(c)
    assert fr["matches_recap_project_bounds"] is True


@pytest.mark.skipif(not os.environ.get("RECAP_MCP_SANIDADE_RCP"),
                    reason="defina RECAP_MCP_SANIDADE_RCP com o caminho do 'Teste MCP.rcp'")
def test_sanidade_teste_mcp_rcp(tmp_path):
    """Projeto real gerado pelo DeCap a partir da grade de 900 pontos (2,9 x 2,9 m)."""
    c = index.open_cloud(tmp_path / "estado", os.environ["RECAP_MCP_SANIDADE_RCP"])
    i = analysis.info(c)
    assert i["total_points"] == 900
    assert i["bounds"]["max"] == pytest.approx([2.9, 2.9, 0.0])
    assert i["coordinate_frame"]["matches_recap_project_bounds"] is True
    assert analysis.query_box(c, None, None, 0)["points_in_region"] == 900
