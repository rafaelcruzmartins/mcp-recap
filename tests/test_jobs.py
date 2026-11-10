from __future__ import annotations

import json
import time

import pytest

from recap_mcp import errors
from recap_mcp.decap import plan_import
from recap_mcp.jobs import JobManager


@pytest.fixture
def scans(unicode_dir):
    out = []
    for n in ("a ç", "b"):
        p = unicode_dir / f"{n}.pts"
        p.write_text("1\n0 0 0 1 2 3 4\n", encoding="utf-8")
        out.append(str(p))
    return out


@pytest.fixture
def manager(tmp_path, fake_launcher):
    m = JobManager(tmp_path / "estado", lambda: fake_launcher)
    yield m
    m.shutdown()


def _run(manager, caps, tmp_path, scans, monkeypatch, mode="success", name="proj", **opts):
    if mode:
        monkeypatch.setenv("FAKE_DECAP_MODE", mode)
    plan = plan_import(caps, str(tmp_path / "saída"), name, scans, **opts)
    job = manager.start_import(plan)
    assert job["status"] == "running" and job["pid"]
    return manager.wait(job["job_id"], timeout=60)


def test_success_lifecycle(manager, caps, tmp_path, scans, monkeypatch):
    args_file = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_DECAP_ARGS_FILE", str(args_file))
    st = _run(manager, caps, tmp_path, scans, monkeypatch, current_coordinate_system="SIRGAS2000.UTM-23S")
    assert st["status"] == "succeeded", st["failure_reasons"]
    assert st["exit_code"] == 0
    assert st["started_at"] and st["finished_at"] and st["elapsed_seconds"] is not None
    v = st["verification"]
    assert v["rcp_exists"] and v["manifest_readable"] and v["scan_count"] == 2
    assert v["missing_rcs_count"] == 0 and v["coordinate_system_applied"] is True
    assert all(r["header_matches"] for r in v["rcs_files"])
    assert st["output_now"]["rcp_exists"]
    assert any(line.startswith("[R]") for line in st["log_tail"])
    # argumentos chegam intactos ao processo (espaços e acentos)
    received = json.loads(args_file.read_text(encoding="utf-8"))
    assert received[-2:] == scans
    assert received[1].endswith("saída")


@pytest.mark.parametrize("mode, reason", [
    ("error_missing", "1025"),
    ("no_output", "não foi criado"),
    ("crash", "0xC0000005"),
    ("scan_failed", "falha os scans"),
])
def test_failures_are_detected_even_with_exit_zero(manager, caps, tmp_path, scans, monkeypatch, mode, reason):
    st = _run(manager, caps, tmp_path, scans, monkeypatch, mode=mode)
    assert st["status"] == "failed"
    assert any(reason in r for r in st["failure_reasons"]), st["failure_reasons"]


def test_coordinate_system_not_applied_is_failure(manager, caps, tmp_path, scans, monkeypatch):
    st = _run(manager, caps, tmp_path, scans, monkeypatch, mode="cs_ignored", current_coordinate_system="XPTO")
    assert st["status"] == "failed"
    assert st["verification"]["coordinate_system_applied"] is False
    assert "current_coordinate_system_invalid" in st["verification"]["free_text_warnings"]


def test_cancel_running_job(manager, caps, tmp_path, scans, monkeypatch):
    monkeypatch.setenv("FAKE_DECAP_MODE", "hang")
    plan = plan_import(caps, str(tmp_path / "o"), "p", scans)
    job = manager.start_import(plan)
    time.sleep(1.0)
    st = manager.status(job["job_id"])
    assert st["status"] == "running" and st["controlled_by_this_server"]
    res = manager.cancel(job["job_id"])
    assert res["status"] == "cancelled"
    assert "parciais" in res["note"]
    with pytest.raises(errors.RecapMcpError) as ei:
        manager.cancel(job["job_id"])
    assert ei.value.code == errors.JOB_NOT_CANCELLABLE


def test_cancel_unknown_job(manager):
    with pytest.raises(errors.RecapMcpError) as ei:
        manager.cancel("nao-existe")
    assert ei.value.code == errors.JOB_NOT_FOUND
    with pytest.raises(errors.RecapMcpError):
        manager.status("../../etc")


def test_concurrency_limit_and_destination_race(tmp_path, fake_launcher, caps, scans, monkeypatch):
    monkeypatch.setenv("FAKE_DECAP_MODE", "hang")
    m = JobManager(tmp_path / "e", lambda: fake_launcher, max_concurrent=1)
    try:
        plan = plan_import(caps, str(tmp_path / "o"), "p", scans)
        plan2 = plan_import(caps, str(tmp_path / "o"), "p", scans)  # validado antes do 1º iniciar
        m.start_import(plan)
        with pytest.raises(errors.RecapMcpError) as ei:
            m.start_import(plan_import(caps, str(tmp_path / "o2"), "q", scans))
        assert ei.value.code == errors.TOO_MANY_JOBS
        m.max_concurrent = 2
        with pytest.raises(errors.RecapMcpError) as ei:
            m.start_import(plan2)
        assert ei.value.code == errors.DESTINATION_EXISTS
    finally:
        m.shutdown()


def test_restart_marks_active_jobs_interrupted(tmp_path, fake_launcher, caps, scans, monkeypatch):
    monkeypatch.setenv("FAKE_DECAP_MODE", "hang")
    m1 = JobManager(tmp_path / "e", lambda: fake_launcher)
    job = m1.start_import(plan_import(caps, str(tmp_path / "o"), "p", scans))
    # simula queda do servidor: o job.json fica "running" como estava antes do fim
    m1._procs[job["job_id"]].kill()
    m1._threads[job["job_id"]].join(10)
    data = json.loads((tmp_path / "e" / "jobs" / job["job_id"] / "job.json").read_text(encoding="utf-8"))
    data["status"] = "running"
    (tmp_path / "e" / "jobs" / job["job_id"] / "job.json").write_text(json.dumps(data), encoding="utf-8")

    m2 = JobManager(tmp_path / "e", lambda: fake_launcher)
    st = m2.status(job["job_id"])
    assert st["status"] == "interrupted"
    assert not st["controlled_by_this_server"]
    assert "reiniciado" in st["failure_reasons"][0]
    with pytest.raises(errors.RecapMcpError) as ei:
        m2.cancel(job["job_id"])
    assert ei.value.code == errors.JOB_NOT_CANCELLABLE
    assert m2.list()["jobs"][0]["status"] == "interrupted"


def test_list_jobs_filter_and_log_limit(manager, caps, tmp_path, scans, monkeypatch):
    st = _run(manager, caps, tmp_path, scans, monkeypatch)
    assert manager.list(status="succeeded")["count"] == 1
    assert manager.list(status="failed")["count"] == 0
    assert len(manager.status(st["job_id"], log_lines=2)["log_tail"]) == 2
    assert manager.status(st["job_id"], log_lines=10_000)["log_tail"]  # limitado internamente


def test_control_file_is_used_for_long_lists(manager, caps, tmp_path, monkeypatch):
    from recap_mcp import decap

    monkeypatch.setattr(decap, "CONTROL_FILE_THRESHOLD_CHARS", 10)
    f = tmp_path / "x y.pts"
    f.write_text("1\n0 0 0\n")
    st = _run(manager, caps, tmp_path, [str(f)], monkeypatch)
    assert st["control_file"] and st["status"] == "succeeded"
    assert "--controlFile" in st["command"]


def test_control_file_refuses_non_ansi_paths(manager, caps, tmp_path, monkeypatch):
    from recap_mcp import decap

    monkeypatch.setattr(decap, "CONTROL_FILE_THRESHOLD_CHARS", 10)
    d = tmp_path / "nuvem_中文"
    d.mkdir()
    f = d / "a.pts"
    f.write_text("1\n0 0 0\n")
    plan = plan_import(caps, str(tmp_path / "o"), "p", [str(f)])
    with pytest.raises(errors.RecapMcpError) as ei:
        manager.start_import(plan)
    assert ei.value.code == errors.LIMIT_EXCEEDED
    assert manager.list()["total"] == 0
