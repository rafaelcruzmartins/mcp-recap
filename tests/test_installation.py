from __future__ import annotations

import pytest

from recap_mcp import installation
from recap_mcp.decap_messages import free_text_warnings, parse_messages, summarize


@pytest.fixture
def no_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(installation, "_registry_products", list)
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(tmp_path / "pf_vazio"))
    monkeypatch.delenv("RECAP_HOME", raising=False)


def test_finds_install_via_recap_home(no_registry, monkeypatch, tmp_path):
    home = tmp_path / "Autodesk ReCap"
    home.mkdir()
    (home / "DeCap.exe").write_bytes(b"MZ")  # nome com caixa diferente
    (home / "ReCap.exe").write_bytes(b"MZ")
    monkeypatch.setenv("RECAP_HOME", str(home))
    inst = installation.find_installation()
    assert inst.found
    assert inst.source == "RECAP_HOME"
    assert inst.decap_exe.lower().endswith("decap.exe")
    assert inst.recap_exe.endswith("ReCap.exe")
    assert inst.decap_file_version is None  # arquivo falso não tem recurso de versão


def test_finds_install_in_program_files(no_registry, monkeypatch, tmp_path):
    pf = tmp_path / "pf"
    folder = pf / "Autodesk" / "Autodesk ReCap"
    folder.mkdir(parents=True)
    (folder / "decap.exe").write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(pf))
    inst = installation.find_installation()
    assert inst.found and inst.source == "Program Files"
    assert inst.recap_exe is None


def test_not_found_reports_searched(no_registry):
    inst = installation.find_installation()
    assert not inst.found
    d = inst.to_dict()
    assert d["decap_exe"] is None and isinstance(d["searched"], list)


def test_parse_help_detects_only_known_flags():
    modes, opts = installation.parse_help("--importWithLicense --decimation --unify --foo --checkLicense")
    assert modes == ["--importWithLicense", "--checkLicense"]
    assert opts == ["--decimation", "--unify"]


def test_capability_probe_with_fake(caps):
    assert caps.help_available
    assert caps.version == "27.0.1.242"
    assert "--importWithLicense" in caps.modes
    assert len(caps.import_options) == 12


def test_check_license_states(fake_launcher, monkeypatch):
    assert installation.check_license(fake_launcher)["state"] == "valida"
    monkeypatch.setenv("FAKE_DECAP_LICENSE", "bad")
    assert installation.check_license(fake_launcher)["state"] == "invalida_ou_indisponivel"


def test_decode_console_handles_utf8_and_ansi():
    assert installation.decode_console("ação".encode()) == "ação"
    ansi = installation.decode_console("ação".encode("cp1252"))
    assert "�" not in ansi or not installation.IS_WINDOWS  # bytes inválidos em UTF-8 não quebram
    assert ansi.startswith("a") and len(ansi) == 4


def test_parse_messages_formats():
    text = (
        "Welcome\r\n[F][C:\\a b\\x.pts][1]\n[P][1 82]\n---\n[P][1 100]\n[P][2 Failed]\n"
        "[E][1025][File 'x' can not be found]\n[M][2049][License Check OK]\n[R][C:\\out\\p.rcp]\n"
    )
    msgs = parse_messages(text)
    kinds = [m["kind"] for m in msgs]
    assert kinds == ["F", "P", "P", "P", "E", "M", "R"]
    s = summarize(msgs)
    assert s["progress_by_scan"] == {"1": 100}
    assert s["failed_scan_indexes"] == ["2"]
    assert s["errors"][0]["code"] == "1025"
    assert s["reported_projects"] == ["C:\\out\\p.rcp"]


def test_free_text_warnings():
    assert free_text_warnings("Current coordinate system not valid. Setting ...") == [
        "current_coordinate_system_invalid"]
    assert free_text_warnings("tudo certo") == []
