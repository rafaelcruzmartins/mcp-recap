"""Testes de integração com o Autodesk ReCap/DeCap REAL instalado nesta máquina.

Executar com:  RECAP_MCP_INTEGRATION=1 uv run pytest -m integration -s
Exigem ReCap Pro instalado e licença ativa. Usam apenas nuvens sintéticas pequenas
geradas em pastas temporárias novas (com espaços e acentos no caminho).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from recap_mcp import installation, projects
from recap_mcp.decap import plan_import
from recap_mcp.jobs import JobManager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RECAP_MCP_INTEGRATION") != "1",
                       reason="defina RECAP_MCP_INTEGRATION=1 para rodar contra o ReCap real"),
]

TIMEOUT = 600


@pytest.fixture(scope="module")
def inst():
    i = installation.find_installation()
    if not i.found:
        pytest.skip("ReCap não encontrado nesta máquina")
    return i


@pytest.fixture(scope="module")
def caps(inst):
    return installation.CapabilityProbe([inst.decap_exe]).get()


@pytest.fixture(scope="module")
def license_ok(inst):
    res = installation.check_license([inst.decap_exe])
    if res["state"] != "valida":
        pytest.skip(f"licença do ReCap não validada: {res}")
    return res


@pytest.fixture
def work(tmp_path) -> Path:
    d = tmp_path / "integração com espaço"
    d.mkdir()
    return d


@pytest.fixture
def manager(tmp_path, inst):
    m = JobManager(tmp_path / "estado", lambda: [inst.decap_exe])
    yield m
    m.shutdown()


def grid_pts(path: Path, n: int = 100, step: float = 0.1, scale: float = 1.0, offset_x: float = 0.0) -> int:
    """Grade plana n x n com espaçamento ``step`` (em metros, multiplicado por ``scale``)."""
    with path.open("w", encoding="ascii") as fh:
        fh.write(f"{n * n}\n")
        for i in range(n):
            for j in range(n):
                fh.write(f"{(i * step + offset_x) * scale:.4f} {j * step * scale:.4f} 0.0000 50 120 120 120\n")
    return n * n


def grid_xyz(path: Path, n: int = 100, step: float = 0.1, scale: float = 1.0) -> int:
    with path.open("w", encoding="ascii") as fh:
        for i in range(n):
            for j in range(n):
                fh.write(f"{i * step * scale:.4f} {j * step * scale:.4f} 0.0000\n")
    return n * n


def run(manager, caps, inputs, out, name, **opts):
    plan = plan_import(caps, str(out), name, [str(p) for p in inputs], **opts)
    job = manager.start_import(plan)
    st = manager.wait(job["job_id"], timeout=TIMEOUT)
    print(json.dumps({k: st[k] for k in ("job_id", "status", "exit_code", "failure_reasons")}, ensure_ascii=False))
    return st


def test_installation_and_capabilities(inst, caps, license_ok):
    assert Path(inst.decap_exe).is_file()
    assert inst.decap_file_version
    assert "--importWithLicense" in caps.modes
    print("ReCap", inst.product_version, "DeCap", caps.version, "opções:", caps.import_options)


def test_import_pts_creates_verified_project(manager, caps, license_ok, work):
    src = work / "grade ação.pts"
    n = grid_pts(src)
    st = run(manager, caps, [src], work / "saída", "Grade Única")
    assert st["exit_code"] == 0
    assert st["status"] == "succeeded", st["failure_reasons"]
    v = st["verification"]
    # existência de arquivo, leitura e efeito são verificações distintas
    assert v["rcp_exists"] and v["manifest_readable"]
    assert v["scan_count"] == 1 and v["total_points"] == n
    assert v["rcs_files"][0]["exists"] and v["rcs_files"][0]["header_matches"]
    info = projects.read_project(st["output"]["expected_rcp"])
    assert info["project_bounds"]["max"][:2] == pytest.approx([9.9, 9.9])
    scans = projects.list_scans(st["output"]["expected_rcp"])
    # todos os .rcs referenciados precisam ser encontrados (caminho com acentos)
    assert scans["missing_rcs_count"] == 0
    print("resolução do .rcs:", scans["scans"][0]["rcs"]["resolved_by"],
          "path com perda:", scans["scans"][0]["rcs"]["manifest_path_lossy"])


def test_two_scans_and_control_file(manager, caps, license_ok, work, monkeypatch):
    from recap_mcp import decap

    a, b = work / "a ç.pts", work / "b.pts"
    grid_pts(a, n=30)
    grid_pts(b, n=30, offset_x=20)
    monkeypatch.setattr(decap, "CONTROL_FILE_THRESHOLD_CHARS", 1)  # força --controlFile
    st = run(manager, caps, [a, b], work / "out2", "Dois")
    assert "--controlFile" in st["command"]
    assert st["status"] == "succeeded", st["failure_reasons"]
    assert st["verification"]["scan_count"] == 2
    assert st["verification"]["total_points"] == 1800


def test_input_unit_mm_scales_the_cloud(manager, caps, license_ok, work):
    """Mesmos números lidos como mm devem gerar uma nuvem 1000x menor que em metros."""
    src = work / "grade.xyz"
    grid_xyz(src, n=50, step=0.2)
    st_m = run(manager, caps, [src], work / "m", "em_metros")
    st_mm = run(manager, caps, [src], work / "mm", "em_mm", input_unit="mm")
    assert st_m["status"] == st_mm["status"] == "succeeded"
    size_m = st_m["verification"]["project_bounds"]["size"][0]
    size_mm = st_mm["verification"]["project_bounds"]["size"][0]
    print("extensão X em m:", size_m, "lida como mm:", size_mm)
    assert size_m == pytest.approx(9.8, rel=1e-3)
    assert size_mm == pytest.approx(size_m / 1000, rel=1e-2)


def test_coordinate_system_is_recorded(manager, caps, license_ok, work):
    src = work / "g.pts"
    grid_pts(src, n=20)
    st = run(manager, caps, [src], work / "cs", "com_cs", current_coordinate_system="SIRGAS2000.UTM-23S")
    assert st["status"] == "succeeded", st["failure_reasons"]
    assert st["verification"]["coordinate_system"] == "SIRGAS2000.UTM-23S"


def test_invalid_coordinate_system_is_reported_as_failure(manager, caps, license_ok, work):
    src = work / "g.pts"
    grid_pts(src, n=20)
    st = run(manager, caps, [src], work / "csx", "cs_invalido", current_coordinate_system="NAO_EXISTE-99")
    assert st["exit_code"] == 0  # o DeCap não sinaliza o erro no código de saída
    assert st["status"] == "failed"
    assert st["verification"]["coordinate_system_applied"] is False
    assert "current_coordinate_system_invalid" in st["verification"]["free_text_warnings"]


def test_garbage_input_is_not_reported_as_success(manager, caps, license_ok, work):
    src = work / "lixo.pts"
    src.write_text("isto não é uma nuvem\nabc def\n", encoding="utf-8")
    st = run(manager, caps, [src], work / "lixo", "lixo")
    print("verificação:", json.dumps(st["verification"], ensure_ascii=False)[:800])
    assert st["status"] == "failed"


def test_decimation_and_clipping_are_accepted(manager, caps, license_ok, work):
    """Mede e registra o efeito. Em nuvens NÃO estruturadas o DeCap 27.0.1 não altera a contagem."""
    src = work / "g.pts"
    n = grid_pts(src)
    dec = run(manager, caps, [src], work / "dec", "dec", decimation_mm=200)
    clip = run(manager, caps, [src], work / "clip", "clip", min_range_m=2, max_range_m=5)
    assert dec["status"] == clip["status"] == "succeeded"
    print("pontos: original", n, "decimação 200 mm", dec["verification"]["total_points"],
          "recorte 2-5 m", clip["verification"]["total_points"])
    log_dec = Path(dec["log_file"]).read_bytes().decode("mbcs", errors="replace")
    log_clip = Path(clip["log_file"]).read_bytes().decode("mbcs", errors="replace")
    assert "Setting decimation to 200 mm" in log_dec
    assert "Setting the min clipping range to 2" in log_clip


def test_unify_outcome_is_verified(manager, caps, license_ok, work):
    a, b = work / "a.pts", work / "b.pts"
    grid_pts(a, n=30)
    grid_pts(b, n=30, offset_x=20)
    st = run(manager, caps, [a, b], work / "uni", "unificado", unify=True)
    print("unify:", st["status"], st["exit_code_description"], st["failure_reasons"])
    if st["status"] == "succeeded":
        assert st["verification"]["scan_count"] == 1
    else:
        assert not st["verification"]["rcp_exists"] or st["failure_reasons"]


def test_cancel_real_process(manager, caps, license_ok, work):
    src = work / "grande.pts"
    grid_pts(src, n=1500, step=0.01)  # 2,25 milhões de pontos
    plan = plan_import(caps, str(work / "cancel"), "cancelado", [str(src)])
    job = manager.start_import(plan)
    time.sleep(3)
    assert manager.status(job["job_id"])["status"] == "running"
    res = manager.cancel(job["job_id"])
    assert res["status"] == "cancelled"
    import subprocess

    out = subprocess.run(["tasklist", "/FI", f"PID eq {job['pid']}"], capture_output=True, text=True, check=False)
    assert str(job["pid"]) not in out.stdout


def test_mcp_stdio_against_real_decap(tmp_path, work, license_ok):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    src = work / "via mcp ç.pts"
    n = grid_pts(src, n=40)
    env = {k: v for k, v in os.environ.items() if k != "RECAP_MCP_DECAP_LAUNCHER"}
    env["RECAP_MCP_STATE_DIR"] = str(tmp_path / "estado")
    params = StdioServerParameters(command=sys.executable, args=["-m", "recap_mcp"], env=env)

    async def body():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            info = (await s.call_tool("check_recap_installation", {"check_license": True})).structuredContent
            assert info["installation"]["found"] and info["license"]["state"] == "valida"
            started = (await s.call_tool("import_scans", {
                "inputs": [str(src)], "output_folder": str(work / "mcp"), "project_name": "via_mcp"})).structuredContent
            for _ in range(TIMEOUT):
                st = (await s.call_tool("get_job_status", {"job_id": started["job_id"]})).structuredContent
                if st["status"] not in ("queued", "running"):
                    break
                await asyncio.sleep(1)
            assert st["status"] == "succeeded", st
            proj = (await s.call_tool("read_project", {"path": st["output"]["expected_rcp"]})).structuredContent
            assert proj["total_points"] == n

    asyncio.run(body())


@pytest.mark.skipif(os.environ.get("RECAP_MCP_INTEGRATION_GUI") != "1",
                    reason="abre a interface do ReCap; defina RECAP_MCP_INTEGRATION_GUI=1")
def test_open_project_in_recap_gui(manager, caps, license_ok, work, inst):
    """Abre um projeto sintético na interface e confirma pelo AutodeskReCap.log; depois fecha o processo aberto."""
    import subprocess

    from recap_mcp import recap_app

    if recap_app.running_recap_pids():
        pytest.skip("o ReCap já está aberto nesta máquina; o teste não interfere em sessões do usuário")
    src = work / "gui.pts"
    grid_pts(src, n=20)
    st = run(manager, caps, [src], work / "gui", "gui")
    assert st["status"] == "succeeded"
    res = recap_app.open_project(inst.recap_exe, Path(st["output"]["expected_rcp"]), wait_for_load_seconds=240)
    try:
        print("abertura:", res["load_confirmed"], res.get("load_check"))
        assert res["load_confirmed"] is True
    finally:
        subprocess.run(["taskkill", "/PID", str(res["pid"]), "/T", "/F"], capture_output=True, check=False)
