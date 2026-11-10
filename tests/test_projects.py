from __future__ import annotations

import os
import zipfile

import pytest
from rcp_fixtures import ScanSpec, make_project, manifest_xml, write_rcp, write_rcs

from recap_mcp import errors, projects


def _err(fn, *a, **k) -> str:
    with pytest.raises(errors.RecapMcpError) as ei:
        fn(*a, **k)
    return ei.value.code


# ------------------------------------------------------------------ find_projects

def test_find_projects_limits_and_types(unicode_dir):
    make_project(unicode_dir, "A", [ScanSpec("s1")])
    deep = unicode_dir / "n1" / "n2" / "n3"
    deep.mkdir(parents=True)
    make_project(deep, "B", [ScanSpec("s2")])
    (unicode_dir / "outro.txt").write_text("x")

    res = projects.find_projects(str(unicode_dir))
    types = sorted(r["type"] for r in res["results"])
    assert types == ["rcp", "rcp", "rcs", "rcs"]
    assert not res["truncated"]

    only_rcp = projects.find_projects(str(unicode_dir), include_rcs=False)
    assert {r["type"] for r in only_rcp["results"]} == {"rcp"}

    shallow = projects.find_projects(str(unicode_dir), max_depth=1)
    assert all("n3" not in r["path"] for r in shallow["results"])

    limited = projects.find_projects(str(unicode_dir), max_results=1)
    assert limited["count"] == 1 and limited["truncated"]


def test_find_projects_errors(tmp_path):
    assert _err(projects.find_projects, str(tmp_path / "nao")) == errors.PATH_NOT_FOUND
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert _err(projects.find_projects, str(f)) == errors.NOT_A_DIRECTORY
    assert _err(projects.find_projects, "relativa") == errors.INVALID_ARGUMENT
    assert _err(projects.find_projects, str(tmp_path), max_depth=99) == errors.INVALID_ARGUMENT


# ------------------------------------------------------------------- read_project

def test_read_project_metadata(unicode_dir):
    rcp = make_project(unicode_dir, "Obra", [ScanSpec("s1", num_points=20000), ScanSpec("s2", num_points=5)],
                       coordinate_system="SIRGAS2000.UTM-23S")
    res = projects.read_project(str(rcp))
    assert res["type"] == "rcp"
    assert res["scan_count"] == 2
    assert res["total_points"] == 20005
    assert res["coordinate_system"] == "SIRGAS2000.UTM-23S"
    assert res["schema_version"] == "1.6.0"
    assert res["save_history"][0]["application"] == "decap"
    assert "UserId" not in str(res["save_history"])  # não expõe nome de usuário
    assert res["project_bounds"]["max"] == [1.0, 0.5, 0.0]
    assert set(res["project_bounds"]["omitted_attributes_as_zero"]) >= {"minx", "maxz"}
    assert res["display_unit"]["unit_type_raw"] == 1
    assert res["support_folder"]["exists"]
    assert res["warnings"] == []


def test_read_project_empty_and_future_schema(tmp_path):
    xml = manifest_xml([], major="2")
    rcp = write_rcp(tmp_path / "v.rcp", xml)
    res = projects.read_project(str(rcp))
    assert res["scan_count"] == 0
    assert any("2.6.0" in w for w in res["warnings"])
    assert any("não contém scans" in w for w in res["warnings"])


def test_read_rcs_header(tmp_path):
    rcs = write_rcs(tmp_path / "s.rcs", 777, "{aaaaaaaa_bbbb_cccc_dddd_eeeeeeeeeeee}")
    res = projects.read_project(str(rcs))
    assert res["type"] == "rcs"
    assert res["num_points"] == 777
    assert res["guid"] == "{aaaaaaaa_bbbb_cccc_dddd_eeeeeeeeeeee}"
    assert res["format_version"] == "3.2"
    assert res["local_data_bounds"]["max"] == [0.5, 0.25, 0.0]


def test_invalid_inputs(tmp_path):
    txt = tmp_path / "a.e57"
    txt.write_bytes(b"x")
    assert _err(projects.read_project, str(txt)) == errors.UNSUPPORTED_FORMAT
    notzip = tmp_path / "n.rcp"
    notzip.write_bytes(b"isto nao e zip")
    assert _err(projects.read_project, str(notzip)) == errors.INVALID_PROJECT
    rcs_as_rcp = tmp_path / "r.rcp"
    rcs_as_rcp.write_bytes(b"ADOCT" + b"\0" * 400)
    with pytest.raises(errors.RecapMcpError) as ei:
        projects.read_project(str(rcs_as_rcp))
    assert ".rcs" in ei.value.message
    bad_rcs = tmp_path / "b.rcs"
    bad_rcs.write_bytes(b"PK" + b"\0" * 400)
    assert _err(projects.read_project, str(bad_rcs)) == errors.UNSUPPORTED_FORMAT
    short_rcs = tmp_path / "c.rcs"
    short_rcs.write_bytes(b"ADOCT" + b"\0" * 10)
    assert _err(projects.read_project, str(short_rcs)) == errors.INVALID_PROJECT
    assert _err(projects.read_project, str(tmp_path / "nao.rcp")) == errors.PATH_NOT_FOUND


def test_zip_without_manifest(tmp_path):
    p = tmp_path / "x.rcp"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("imagem.png", b"x")
    assert _err(projects.read_project, str(p)) == errors.INVALID_PROJECT


def test_malformed_and_dtd_and_wrong_root(tmp_path):
    assert _err(projects.read_project, str(write_rcp(tmp_path / "m.rcp", "<Autodesk><a></Autodesk>"))) == \
        errors.INVALID_PROJECT
    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]><Autodesk>&a;</Autodesk>'
    assert _err(projects.read_project, str(write_rcp(tmp_path / "d.rcp", bomb))) == errors.INVALID_PROJECT
    assert _err(projects.read_project, str(write_rcp(tmp_path / "w.rcp", "<Outro/>"))) == errors.UNSUPPORTED_FORMAT


def test_manifest_size_limit(tmp_path, monkeypatch):
    rcp = make_project(tmp_path, "g", [ScanSpec("s")])
    monkeypatch.setattr(projects, "MAX_MANIFEST_BYTES", 100)
    assert _err(projects.read_project, str(rcp)) == errors.LIMIT_EXCEEDED


def test_zip_entries_limit_and_no_extraction(tmp_path, monkeypatch):
    """Entradas com '../' no ZIP nunca são extraídas: só o manifesto da raiz é lido em memória."""
    rcp = write_rcp(tmp_path / "t.rcp", manifest_xml([ScanSpec("s")]),
                    extra={"../../fora.txt": b"x", "sub/../../x.xml": b"<Autodesk/>"})
    res = projects.read_project(str(rcp))
    assert res["scan_count"] == 1
    assert not (tmp_path.parent / "fora.txt").exists()
    monkeypatch.setattr(projects, "MAX_ZIP_ENTRIES", 1)
    assert _err(projects.read_project, str(rcp)) == errors.LIMIT_EXCEEDED


def test_cp1252_manifest_is_accepted(tmp_path):
    rcp = write_rcp(tmp_path / "e.rcp", manifest_xml([ScanSpec("seção")]), encoding="cp1252")
    res = projects.read_project(str(rcp))
    assert res["manifest"]["manifest_encoding"] == "cp1252"
    assert projects.list_scans(str(rcp))["scans"][0]["name"] == "seção"


# --------------------------------------------------------------------- list_scans

def test_list_scans_with_missing_rcs(unicode_dir):
    rcp = make_project(unicode_dir, "P", [ScanSpec("ok", num_points=10), ScanSpec("falta")], create_rcs=[True, False])
    res = projects.list_scans(str(rcp))
    assert res["scan_count"] == 2
    assert res["missing_rcs_count"] == 1
    ok, falta = res["scans"]
    assert ok["rcs"]["exists"] and ok["rcs"]["resolved_by"] == "Path"
    assert ok["rcs"]["header_check"] == {"num_points": 10, "matches_manifest_points": True, "matches_manifest_id": True}
    assert not falta["rcs"]["exists"] and falta["rcs"]["tried"]
    assert ok["source_scan"]["exists"] is False  # caminho fictício da entrada original
    assert ok["registration"]["is_registered"] is True


def test_list_scans_lossy_path_falls_back_to_support(tmp_path):
    """Caminho gravado corrompido (U+FFFD) ou inválido: o .rcs é achado na pasta Support."""
    s = ScanSpec("nuvem", num_points=5, manifest_path="C:/sa\ufffdda/P Support\\nuvem.rcs",
                 raw_scan_path="C:\\entrada \ufffd\ufffdo\\nuvem.pts")
    write_rcs(tmp_path / "P Support" / "nuvem.rcs", 5, s.guid)
    rcp = write_rcp(tmp_path / "P.rcp", manifest_xml([s]))
    scan = projects.list_scans(str(rcp))["scans"][0]
    assert scan["rcs"]["exists"] and scan["rcs"]["resolved_by"] == "pasta Support"
    assert scan["rcs"]["manifest_path_lossy"] is True
    assert scan["source_scan"] == {"path": s.raw_scan_path, "path_lossy": True, "exists": None}


def test_list_scans_relative_path(tmp_path):
    s = ScanSpec("r", num_points=3, manifest_path="Z:/nao/existe/r.rcs", relative_path=".\\dados\\r.rcs")
    write_rcs(tmp_path / "dados" / "r.rcs", 3, s.guid)
    rcp = write_rcp(tmp_path / "R.rcp", manifest_xml([s]))
    assert projects.list_scans(str(rcp))["scans"][0]["rcs"]["resolved_by"] == "RelativePath"


def test_list_scans_header_mismatch_and_truncation(tmp_path):
    scans = [ScanSpec(f"s{i}", num_points=100) for i in range(3)]
    rcp = make_project(tmp_path, "M", scans)
    write_rcs(tmp_path / "M Support" / "s0.rcs", 99, scans[0].guid)
    res = projects.list_scans(str(rcp), max_scans=2)
    assert res["returned"] == 2 and res["truncated"]
    assert res["scans"][0]["rcs"]["header_check"]["matches_manifest_points"] is False
    assert _err(projects.list_scans, str(tmp_path / "M Support" / "s0.rcs")) == errors.UNSUPPORTED_FORMAT


def test_no_points_are_returned(tmp_path):
    rcp = make_project(tmp_path, "N", [ScanSpec("s", num_points=10_000_000)])
    res = projects.list_scans(str(rcp))
    assert len(str(res)) < 5000
    assert os.path.getsize(rcp) < 10000
