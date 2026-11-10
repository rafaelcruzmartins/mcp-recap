from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

FAKE_DECAP = TESTS / "fake_decap.py"


@pytest.fixture
def fake_launcher() -> list[str]:
    return [sys.executable, str(FAKE_DECAP)]


@pytest.fixture
def fake_env(monkeypatch, tmp_path, fake_launcher):
    """Aponta o servidor para o DeCap falso e para um diretório de estado temporário."""
    monkeypatch.setenv("RECAP_MCP_DECAP_LAUNCHER", json.dumps(fake_launcher))
    monkeypatch.setenv("RECAP_MCP_STATE_DIR", str(tmp_path / "estado"))
    monkeypatch.delenv("FAKE_DECAP_MODE", raising=False)
    return tmp_path


@pytest.fixture
def caps(fake_launcher):
    from recap_mcp.installation import CapabilityProbe

    return CapabilityProbe(fake_launcher).get()


@pytest.fixture
def unicode_dir(tmp_path) -> Path:
    d = tmp_path / "pasta com espaço ção"
    d.mkdir()
    return d
