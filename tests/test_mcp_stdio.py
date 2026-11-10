"""Handshake MCP, tools/list e chamadas reais pelo transporte STDIO.

O servidor roda como subprocesso (``python -m recap_mcp``) apontando para o DeCap
falso, de modo que o teste não depende do ReCap instalado.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rcp_fixtures import ScanSpec, make_project

EXPECTED_TOOLS = {
    "check_recap_installation", "find_projects", "read_project", "list_scans", "import_scans",
    "get_job_status", "list_jobs", "cancel_job", "open_project_in_recap",
    # leitura de pontos (Plano B)
    "point_cloud_info", "index_point_cloud", "query_box", "z_histogram", "section", "render_view", "profile",
    "nearest_points", "measure", "export_subset",
}


def test_point_tools_return_image_over_stdio(tmp_path, fake_launcher):
    pts = tmp_path / "nuvem ç.pts"
    pts.write_text("4\n0 0 0 1 10 20 30\n1 0 0 1 10 20 30\n0 1 0 1 10 20 30\n1 1 1 1 10 20 30\n", encoding="ascii")

    async def body(session, _):
        r = await session.call_tool("render_view", {"source": str(pts), "view": "top", "width_px": 256})
        assert not r.isError
        kinds = [c.type for c in r.content]
        assert kinds == ["text", "image"] and r.content[1].mimeType == "image/png"
        assert r.structuredContent["points_in_region"] == 4
        assert r.structuredContent["coordinate_frame"]["coordinates"] == "arquivo de origem"
        q = _payload(await session.call_tool("query_box", {"source": str(pts), "sample_size": 2}))
        assert q["points_in_region"] == 4 and q["sample"]["size"] == 2
        bad = _error(await session.call_tool("query_box", {"source": str(pts), "sample_size": 10, "min": [1, 1, 1],
                                                           "max": [0, 0, 0]}))
        assert bad["error"]["code"] == "invalid_argument"

    asyncio.run(_session(_params(tmp_path, fake_launcher), body))


def _params(tmp_path, fake_launcher, extra_env=None) -> StdioServerParameters:
    env = dict(os.environ)
    env.update({
        "RECAP_MCP_DECAP_LAUNCHER": json.dumps(fake_launcher),
        "RECAP_MCP_STATE_DIR": str(tmp_path / "estado"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra_env or {})
    return StdioServerParameters(command=sys.executable, args=["-m", "recap_mcp"], env=env)


def _payload(result) -> dict:
    if result.structuredContent is not None:
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def _error(result) -> dict:
    assert result.isError, result
    return json.loads(result.content[0].text)


async def _session(params, body):
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        return await body(session, init)


def test_handshake_list_and_calls(tmp_path, fake_launcher, unicode_dir):
    rcp = make_project(unicode_dir, "Projeto", [ScanSpec("s1", num_points=42)])
    scan = unicode_dir / "entrada ç.pts"
    scan.write_text("1\n0 0 0 1 2 3 4\n", encoding="utf-8")

    async def body(session: ClientSession, init):
        assert init.serverInfo.name == "recap"
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == EXPECTED_TOOLS
        by_name = {t.name: t for t in tools.tools}
        assert by_name["read_project"].annotations.readOnlyHint is True
        assert by_name["cancel_job"].annotations.destructiveHint is True
        assert "inputs" in by_name["import_scans"].inputSchema["properties"]
        # nenhuma ferramenta de shell/código arbitrário
        assert not any(k in n for n in names for k in ("exec", "shell", "python", "code"))

        info = _payload(await session.call_tool("check_recap_installation", {"check_license": True}))
        assert info["license"]["state"] == "valida"
        assert "--decimation" in info["decap"]["import_options"]

        found = _payload(await session.call_tool("find_projects", {"folder": str(unicode_dir)}))
        assert found["count"] == 2

        proj = _payload(await session.call_tool("read_project", {"path": str(rcp)}))
        assert proj["total_points"] == 42

        sc = _payload(await session.call_tool("list_scans", {"path": str(rcp)}))
        assert sc["scans"][0]["rcs"]["exists"] is True

        err = _error(await session.call_tool("read_project", {"path": str(unicode_dir / "nao.rcp")}))
        assert err == {"ok": False, "error": {"code": "path_not_found", "message": err["error"]["message"],
                                               "details": err["error"]["details"]}}

        dry = _payload(await session.call_tool("import_scans", {
            "inputs": [str(scan)], "output_folder": str(unicode_dir / "saída"), "project_name": "novo",
            "decimation_mm": 10, "dry_run": True}))
        assert dry["dry_run"] and dry["command"][-1] == str(scan)
        assert not (unicode_dir / "saída").exists()

        overwrite = _error(await session.call_tool("import_scans", {
            "inputs": [str(scan)], "output_folder": str(unicode_dir), "project_name": "Projeto"}))
        assert overwrite["error"]["code"] == "destination_exists"

        started = _payload(await session.call_tool("import_scans", {
            "inputs": [str(scan)], "output_folder": str(unicode_dir / "saída"), "project_name": "novo"}))
        job_id = started["job_id"]
        for _ in range(100):
            st = _payload(await session.call_tool("get_job_status", {"job_id": job_id}))
            if st["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.2)
        assert st["status"] == "succeeded", st
        assert st["verification"]["scan_count"] == 1

        jobs = _payload(await session.call_tool("list_jobs", {}))
        assert jobs["jobs"][0]["job_id"] == job_id

        nc = _error(await session.call_tool("cancel_job", {"job_id": job_id}))
        assert nc["error"]["code"] == "job_not_cancellable"

        bad = _error(await session.call_tool("open_project_in_recap", {"path": str(scan)}))
        assert bad["error"]["code"] == "unsupported_format"

    asyncio.run(_session(_params(tmp_path, fake_launcher), body))


def test_cancel_via_stdio(tmp_path, fake_launcher, unicode_dir):
    scan = unicode_dir / "x.pts"
    scan.write_text("1\n0 0 0\n")

    async def body(session, _):
        started = _payload(await session.call_tool("import_scans", {
            "inputs": [str(scan)], "output_folder": str(unicode_dir / "o"), "project_name": "p"}))
        await asyncio.sleep(1.0)
        res = _payload(await session.call_tool("cancel_job", {"job_id": started["job_id"]}))
        assert res["status"] == "cancelled"

    asyncio.run(_session(_params(tmp_path, fake_launcher, {"FAKE_DECAP_MODE": "hang"}), body))


@pytest.mark.parametrize("args", [["--version"], ["--help"]])
def test_cli_does_not_start_server(args):
    import subprocess

    out = subprocess.run([sys.executable, "-m", "recap_mcp", *args], capture_output=True, text=True, timeout=60,
                         check=False)
    assert out.returncode == 0 and "mcp-recap" in out.stdout
