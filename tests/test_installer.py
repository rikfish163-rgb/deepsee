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


def test_restore_backups(tmp_path, monkeypatch):
    """回退:从 .deepsee.bak 恢复被修改的配置文件。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True)
    settings = claude_dir / "settings.json"
    settings.write_text('{"modified": true}')
    (claude_dir / "settings.json.deepsee.bak").write_text('{"original": true}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))
    restored = installer.restore_backups()
    assert str(settings) in restored
    assert json.loads(settings.read_text()) == {"original": True}


def test_setup_wizard_noninteractive_demo(tmp_path, monkeypatch):
    """非交互跑通向导:选 demo 供应商,不触碰任何真实配置。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path / ".deepsee"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))

    class _NoTty:
        def isatty(self):
            return False

    monkeypatch.setattr(installer.sys, "stdin", _NoTty())
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    monkeypatch.chdir(tmp_path)

    # 供应商选择返回 demo 的序号,其余字段用默认值
    demo_idx = list(installer.PROVIDERS.keys()).index("demo") + 1
    monkeypatch.setattr(
        installer, "_ask",
        lambda prompt, default="": (str(demo_idx) if "选择" in prompt else default),
    )

    assert installer.setup_wizard() == 0

    from deepsee.config import load

    cfg = load()
    assert cfg["vision"]["chain"][0]["provider"] == "demo"
    # 只写了配置 + 项目 .mcp.json,未动真实 home 的任何文件
    assert (tmp_path / ".deepsee" / "config.json").exists()
    assert (tmp_path / ".mcp.json").exists()
