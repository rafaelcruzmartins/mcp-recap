from __future__ import annotations

import subprocess

import pytest

from recap_mcp import errors, recap_app

ORIGINAL_RUNNING_PIDS = recap_app.running_recap_pids


class FakeProc:
    def __init__(self, code=None):
        self.pid = 4321
        self.returncode = code

    def poll(self):
        return self.returncode


@pytest.fixture(autouse=True)
def no_running_recap(monkeypatch):
    monkeypatch.setattr(recap_app, "running_recap_pids", list)


def test_refuses_when_recap_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(recap_app, "running_recap_pids", lambda: [999])
    called = []
    with pytest.raises(errors.RecapMcpError) as ei:
        recap_app.open_project("ReCap.exe", tmp_path / "p.rcp", popen=lambda *a, **k: called.append(1))
    assert ei.value.code == errors.RECAP_ALREADY_RUNNING
    assert ei.value.details["running_pids"] == [999] and not called
    res = recap_app.open_project("ReCap.exe", tmp_path / "p.rcp", allow_second_instance=True,
                                 popen=lambda *a, **k: FakeProc())
    assert res["ok"]


def test_running_recap_pids_reads_tasklist(monkeypatch):
    class Out:
        stdout = '"ReCap.exe","38748","Console","1","500.000 K"\r\n"INFO: nenhuma tarefa","x"\r\n'

    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return Out()

    monkeypatch.setattr(recap_app.os, "name", "nt")
    monkeypatch.setattr(recap_app.subprocess, "run", run)
    assert ORIGINAL_RUNNING_PIDS() == [38748]
    assert seen["cmd"][0] == "tasklist"


@pytest.fixture
def recap_log(tmp_path, monkeypatch):
    base = tmp_path / "local"
    log = base / "Autodesk" / "Autodesk ReCap" / "AutodeskReCap.log"
    log.parent.mkdir(parents=True)
    log.write_text('event="SimpleEvent", message="antigo"\n', encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    return log


def test_launch_uses_argument_list_without_detached(recap_log, tmp_path):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return FakeProc()

    proj = tmp_path / "pasta ç" / "p.rcp"
    res = recap_app.open_project("C:/X/ReCap.exe", proj, popen=popen)
    assert seen["cmd"] == ["C:/X/ReCap.exe", str(proj)]
    assert seen["kw"]["shell"] is False
    assert not seen["kw"]["creationflags"] & getattr(subprocess, "DETACHED_PROCESS", 0x8)
    assert res["load_confirmed"] is None and res["pid"] == 4321


def test_load_confirmed_from_recap_log(recap_log, tmp_path):
    proj = tmp_path / "pasta ç" / "p.rcp"

    def sleep(_):
        with recap_log.open("a", encoding="utf-8") as fh:
            fh.write(f'event="SimpleEvent", message="[POINTCLOUD] Project loading success - {proj.as_posix()}"\n')

    res = recap_app.open_project("ReCap.exe", proj, wait_for_load_seconds=30, sleep=sleep,
                                 popen=lambda *a, **k: FakeProc())
    assert res["load_confirmed"] is True


def test_old_log_entries_are_ignored_and_timeout(recap_log, tmp_path):
    proj = tmp_path / "p.rcp"
    with recap_log.open("a", encoding="utf-8") as fh:
        fh.write(f'message="[POINTCLOUD] Project loading success - {proj.as_posix()}"\n')
    res = recap_app.open_project("ReCap.exe", proj, wait_for_load_seconds=6, sleep=lambda s: None,
                                 popen=lambda *a, **k: FakeProc())
    assert res["load_confirmed"] is None and "nenhum registro" in res["load_check"]


def test_process_exit_before_load(recap_log, tmp_path):
    res = recap_app.open_project("ReCap.exe", tmp_path / "p.rcp", wait_for_load_seconds=9, sleep=lambda s: None,
                                 popen=lambda *a, **k: FakeProc(code=1))
    assert res["load_confirmed"] is False


def test_invalid_wait_and_launch_failure(recap_log, tmp_path):
    with pytest.raises(errors.RecapMcpError):
        recap_app.open_project("ReCap.exe", tmp_path / "p.rcp", wait_for_load_seconds=999)

    def boom(*a, **k):
        raise OSError("não encontrado")

    with pytest.raises(errors.RecapMcpError) as ei:
        recap_app.open_project("ReCap.exe", tmp_path / "p.rcp", popen=boom)
    assert ei.value.code == errors.LAUNCH_FAILED
