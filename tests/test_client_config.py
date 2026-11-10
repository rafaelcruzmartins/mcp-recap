"""Geradores de configuração testados apenas com arquivos temporários."""

from __future__ import annotations

import json
import tomllib

import pytest

from recap_mcp import client_config as cc

ENTRY = cc.server_entry("C:/Users/x/.local/bin/uv.exe", cc.repo_root())


def _backups(path):
    return sorted(path.parent.glob(path.name + ".bak-*"))


def test_json_preserves_other_servers_and_backs_up(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    original = {"mcpServers": {"revit": {"command": "uv", "args": ["run", "main.py"]}}, "preferences": {"x": 1}}
    cfg.write_text(json.dumps(original), encoding="utf-8")
    res = cc.update_json(cfg, "recap", ENTRY)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["revit"] == original["mcpServers"]["revit"]
    assert data["preferences"] == {"x": 1}
    assert data["mcpServers"]["recap"] == ENTRY
    assert res["changed"] and res["backup"] and len(_backups(cfg)) == 1
    assert json.loads(_backups(cfg)[0].read_text(encoding="utf-8")) == original
    assert not cfg.read_bytes().startswith(b"\xef\xbb\xbf")


def test_json_idempotent_and_dry_run(tmp_path):
    cfg = tmp_path / "c.json"
    dry = cc.update_json(cfg, "recap", ENTRY, dry_run=True)
    assert dry["changed"] and not cfg.exists()
    cc.update_json(cfg, "recap", ENTRY)
    again = cc.update_json(cfg, "recap", ENTRY)
    assert not again["changed"] and again["backup"] is None
    assert _backups(cfg) == []  # arquivo novo não precisa de backup


def test_json_with_bom_is_read(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {}}).encode())
    cc.update_json(cfg, "recap", ENTRY)
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["recap"] == ENTRY


@pytest.mark.parametrize("content", ['{"mcpServers": {', "[1, 2]", '{"mcpServers": []}'])
def test_invalid_json_is_not_touched(tmp_path, content):
    cfg = tmp_path / "c.json"
    cfg.write_text(content, encoding="utf-8")
    with pytest.raises(cc.ConfigError):
        cc.update_json(cfg, "recap", ENTRY)
    assert cfg.read_text(encoding="utf-8") == content
    assert _backups(cfg) == []


CODEX_BEFORE = """model = "gpt-x"

[mcp_servers.revit]
command = "uv"
args = ["run", "main.py"]

[mcp_servers.recap]
command = "velho"

[mcp_servers.recap.env]
A = "1"

[profiles.dev]
model = "y"
"""


def test_codex_replaces_only_own_section(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(CODEX_BEFORE, encoding="utf-8")
    res = cc.update_codex(cfg, "recap", ENTRY)
    after = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert after["model"] == "gpt-x"
    assert after["profiles"] == {"dev": {"model": "y"}}
    assert after["mcp_servers"]["revit"] == {"command": "uv", "args": ["run", "main.py"]}
    assert after["mcp_servers"]["recap"]["args"] == ENTRY["args"]
    assert "env" not in after["mcp_servers"]["recap"]
    assert res["replaced"] and res["backup"]
    assert cc.update_codex(cfg, "recap", ENTRY)["changed"] is False


def test_codex_new_file_and_invalid(tmp_path):
    cfg = tmp_path / "novo" / "config.toml"
    cc.update_codex(cfg, "recap", ENTRY)
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["mcp_servers"]["recap"]["command"] == ENTRY["command"]
    bad = tmp_path / "bad.toml"
    bad.write_text("[x\n", encoding="utf-8")
    with pytest.raises(cc.ConfigError):
        cc.update_codex(bad, "recap", ENTRY)
    assert bad.read_text(encoding="utf-8") == "[x\n"


def test_claude_desktop_paths_include_claude_data(tmp_path, monkeypatch):
    local, roaming = tmp_path / "local", tmp_path / "roaming"
    conta = local / "Claude-Data" / "claude_abc123"
    conta.mkdir(parents=True)
    (conta / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    paths = cc.default_paths("claude-desktop")
    assert paths[0] == roaming / "Claude" / "claude_desktop_config.json"
    assert conta / "claude_desktop_config.json" in paths


def test_claude_code_command_is_list():
    cmd = cc.claude_code_command("recap", ENTRY)
    assert cmd[:6] == ["claude", "mcp", "add", "--scope", "user", "recap"]
    assert cmd[6] == "--" and cmd[-1] == "mcp-recap"


def test_cli_with_explicit_config(tmp_path, capsys):
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text('{"mcpServers": {"outro": {"command": "x"}}}', encoding="utf-8")
    code = cc.main(["--client", "antigravity", "--config", str(cfg), "--uv", str(uv)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"]
    assert set(json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]) == {"outro", "recap"}
    assert cc.main(["--client", "codex", "--uv", str(tmp_path / "nao.exe"), "--config", str(cfg)]) == 2
