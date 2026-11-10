from __future__ import annotations

import pytest

from recap_mcp import errors
from recap_mcp.decap import CONTROL_FILE_THRESHOLD_CHARS, plan_import
from recap_mcp.installation import Capabilities


def _code(exc_info) -> str:
    return exc_info.value.code


@pytest.fixture
def scan(unicode_dir):
    p = unicode_dir / "nuvem ação.pts"
    p.write_text("1\n0 0 0 1 2 3 4\n", encoding="utf-8")
    return p


def test_minimal_plan_args_are_a_list_with_paths_intact(caps, scan, tmp_path):
    out = tmp_path / "saída nova"
    plan = plan_import(caps, str(out), "Projeto Á", [str(scan)])
    args = plan.build_args()
    assert args[:3] == ["--importWithLicense", str(out), "Projeto Á"]
    assert args[-1] == str(scan)  # um único argumento, com espaços e acentos
    assert plan.expected_rcp == out / "Projeto Á.rcp"
    assert plan.expected_support == out / "Projeto Á Support"


def test_all_options_are_built(caps, tmp_path):
    e57 = tmp_path / "a.e57"
    e57.write_bytes(b"x")
    plan = plan_import(
        caps, str(tmp_path / "o"), "p", [str(e57)], decimation_mm=5, min_range_m=1, max_range_m=30,
        unify=True, normalize_intensity=False, input_unit="mm", current_coordinate_system="UTM84-23S",
        target_coordinate_system="SIRGAS2000.UTM-23S", e57_enable_image_import=True, e57_level_image=True,
    )
    assert plan.options == [
        "--decimation", "5", "--minRangeClipping", "1", "--maxRangeClipping", "30", "--unify",
        "--normalizeIntensity", "0", "--inputUnitType", "0", "--currentCoordinateSystem", "UTM84-23S",
        "--targetCoordinateSystem", "SIRGAS2000.UTM-23S", "--e57EnableImageImport", "--e57LevelImage",
    ]
    assert plan.requested["input_unit"] == "mm"
    assert any("--unify" in w for w in plan.warnings)


@pytest.mark.parametrize("kwargs, code", [
    ({"min_range_m": 0.3}, errors.INVALID_ARGUMENT),            # DeCap trunca decimais
    ({"min_range_m": 5, "max_range_m": 2}, errors.INVALID_ARGUMENT),
    ({"min_range_m": -1}, errors.INVALID_ARGUMENT),
    ({"decimation_mm": 0}, errors.INVALID_ARGUMENT),
    ({"decimation_mm": float("nan")}, errors.INVALID_ARGUMENT),
    ({"decimation_mm": True}, errors.INVALID_ARGUMENT),
    ({"input_unit": "km"}, errors.INVALID_ARGUMENT),
    ({"current_coordinate_system": "UTM 23S; del"}, errors.INVALID_ARGUMENT),
    ({"target_coordinate_system": "LL84"}, errors.INVALID_ARGUMENT),
    ({"e57_level_image": True}, errors.INVALID_ARGUMENT),       # entrada não é .e57
    ({"normalize_intensity": "sim"}, errors.INVALID_ARGUMENT),
])
def test_option_validation(caps, scan, tmp_path, kwargs, code):
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(tmp_path / "o"), "p", [str(scan)], **kwargs)
    assert _code(ei) == code


def test_option_not_in_help_is_refused(scan, tmp_path):
    caps = Capabilities(True, ["--importWithLicense"], ["--decimation"], "x")
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(tmp_path / "o"), "p", [str(scan)], unify=True)
    assert _code(ei) == errors.OPTION_UNSUPPORTED


def test_without_import_with_license_mode(scan, tmp_path):
    caps = Capabilities(True, ["--import"], [], "x")
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(tmp_path / "o"), "p", [str(scan)])
    assert _code(ei) == errors.OPTION_UNSUPPORTED


@pytest.mark.parametrize("name", ["", "a/b", "a:b", "con", "x" * 121, "nome.", "a\nb", 'a"b'])
def test_invalid_project_names(caps, scan, tmp_path, name):
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(tmp_path / "o"), name, [str(scan)])
    assert _code(ei) == errors.INVALID_ARGUMENT


def test_rcp_suffix_is_stripped(caps, scan, tmp_path):
    assert plan_import(caps, str(tmp_path / "o"), "proj.rcp", [str(scan)]).project_name == "proj"


def test_never_overwrites_existing_project(caps, scan, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "p.rcp").write_bytes(b"x")
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(out), "p", [str(scan)])
    assert _code(ei) == errors.DESTINATION_EXISTS


def test_never_reuses_existing_support_folder(caps, scan, tmp_path):
    out = tmp_path / "o"
    (out / "p Support").mkdir(parents=True)
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(out), "p", [str(scan)])
    assert _code(ei) == errors.DESTINATION_EXISTS


def test_destination_parent_must_exist(caps, scan, tmp_path):
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(tmp_path / "nao" / "existe"), "p", [str(scan)])
    assert _code(ei) == errors.PATH_NOT_FOUND
    with pytest.raises(errors.RecapMcpError):
        plan_import(caps, str(tmp_path / "novo"), "p", [str(scan)], create_output_folder=False)


def test_destination_is_a_file(caps, scan, tmp_path):
    f = tmp_path / "arquivo"
    f.write_text("x")
    with pytest.raises(errors.RecapMcpError) as ei:
        plan_import(caps, str(f), "p", [str(scan)])
    assert _code(ei) == errors.NOT_A_DIRECTORY


def test_input_validation(caps, scan, tmp_path):
    bad = tmp_path / "virus.exe"
    bad.write_bytes(b"MZ")
    cases = [
        ([], errors.INVALID_ARGUMENT),
        ([str(tmp_path / "nao.pts")], errors.PATH_NOT_FOUND),
        ([str(tmp_path)], errors.NOT_A_FILE),
        ([str(bad)], errors.UNSUPPORTED_FORMAT),
        (["relativo.pts"], errors.INVALID_ARGUMENT),
        ([str(scan), str(scan)], errors.INVALID_ARGUMENT),
    ]
    for inputs, code in cases:
        with pytest.raises(errors.RecapMcpError) as ei:
            plan_import(caps, str(tmp_path / "o"), "p", inputs)
        assert _code(ei) == code, inputs


def test_unverified_format_warns(caps, tmp_path):
    las = tmp_path / "a.las"
    las.write_bytes(b"LASF")
    plan = plan_import(caps, str(tmp_path / "o"), "p", [str(las)])
    assert plan.warnings and "não verificado" in plan.warnings[0]


def test_arguments_cannot_inject_options(caps, tmp_path):
    """Um arquivo chamado '--unify.pts' continua sendo caminho absoluto, nunca uma opção."""
    tricky = tmp_path / "--unify.pts"
    tricky.write_text("1\n0 0 0\n")
    plan = plan_import(caps, str(tmp_path / "o"), "p", [str(tricky)])
    assert "--unify" not in plan.options
    assert plan.build_args()[-1] == str(tricky)


def test_control_file_when_command_line_is_long(caps, tmp_path):
    files = []
    for i in range(int(CONTROL_FILE_THRESHOLD_CHARS / 150) + 5):
        f = tmp_path / ("n" * 120 + f"{i}.pts")
        f.write_text("1\n0 0 0\n")
        files.append(str(f))
    plan = plan_import(caps, str(tmp_path / "o"), "p", files)
    assert plan.needs_control_file()
    args = plan.build_args(tmp_path / "ctl.txt")
    assert args[-2:] == ["--controlFile", str(tmp_path / "ctl.txt")]
