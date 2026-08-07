"""installer 非交互逻辑测试(不触碰真实 agent 配置)。"""

import json
from pathlib import Path

from deepsee import installer


def test_detect_agents_keys():
    d = installer.detect_agents()
    assert set(d) == {"claude_code", "codex", "opencode", "cursor"}
    assert all(isinstance(v, bool) for v in d.values())


def test_deepsee_command_falls_back_to_module(monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    cmd = installer._deepsee_command()
    assert cmd.endswith("-m deepsee")


def test_backup_creates_copy(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"a":1}')
    bak = installer._backup(p)
    assert bak is not None and bak.exists()
    assert bak.read_text() == '{"a":1}'
    assert installer._backup(tmp_path / "missing.json") is None


def test_skill_md_content():
    assert installer.SKILL_MD.startswith("---")
    assert "deepsee see" in installer.SKILL_MD
    assert "deepsee gen" in installer.SKILL_MD


def test_uninstall_claude_hook_noop_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home",
                        classmethod(lambda cls, *a: tmp_path))
    # ~/.claude/settings.json 不存在 → 不报错
    assert installer.uninstall_claude_hook() is False


def test_install_claude_hook_adds_entry(tmp_path, monkeypatch):
    home = tmp_path
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/bin/deepsee")

    ok = installer.install_claude_hook()
    assert ok
    data = json.loads(settings.read_text())
    entries = data["hooks"]["PreToolUse"]
    assert any("deepsee" in e["hooks"][0]["command"] for e in entries)
    # 幂等:再装一次不重复
    installer.install_claude_hook()
    data2 = json.loads(settings.read_text())
    assert len(data2["hooks"]["PreToolUse"]) == 1


def test_install_claude_hook_idempotent_removal(tmp_path, monkeypatch):
    home = tmp_path
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Read", "hooks": [{"type": "command", "command": "/x/deepsee hook"}]}
    ]}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    assert installer.uninstall_claude_hook() is True
    data = json.loads(settings.read_text())
    assert data["hooks"]["PreToolUse"] == []
