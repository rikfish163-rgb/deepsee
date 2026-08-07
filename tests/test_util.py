"""util 工具函数测试。"""

import base64
import json
import os
import tempfile
from pathlib import Path

from deepsee import util


def test_is_image_path():
    assert util.is_image_path("a.png")
    assert util.is_image_path("path/to/x.JPEG")
    assert not util.is_image_path("a.txt")
    assert not util.is_image_path("README.md")


def test_read_image_b64_is_data_uri(tmp_path):
    p = tmp_path / "t.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake")
    uri = util.read_image_b64(p)
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]).startswith(b"\x89PNG")


def test_safe_join():
    assert util.safe_join("https://x.com/v1", "chat/completions") == "https://x.com/v1/chat/completions"
    assert util.safe_join("https://x.com/v1/", "/chat") == "https://x.com/v1/chat"
    assert util.safe_join("https://x.com", "") == "https://x.com"


def test_config_home_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path))
    assert util.config_home() == tmp_path


def test_latest_session_image_claude(tmp_path, monkeypatch):
    root = tmp_path / "proj" / ".claude" / "projects"
    root.mkdir(parents=True)
    log = root / "sess" / "x.jsonl"
    log.parent.mkdir()
    img_bytes = b"\x89PNG\r\n\x1a\nfake"
    log.write_text(
        json.dumps({
            "message": {"content": [
                {"type": "image", "source": {"type": "base64", "data": base64.b64encode(img_bytes).decode()}}
            ]}
        }) + "\n"
    )
    # 隔离 home,避免扫到真实 ~/.claude 日志
    monkeypatch.setattr(util.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    p = util.latest_session_image()
    assert p is not None and p.read_bytes() == img_bytes


def test_b64_bytes_variants():
    raw = base64.b64encode(b"hello")
    assert util._b64_bytes(raw.decode()) == b"hello"
    assert util._b64_bytes("data:image/png;base64," + raw.decode()) == b"hello"
    assert util._b64_bytes("@@not-base64@@") == b""


def test_truncate():
    assert util.truncate("x" * 500) == "x" * 397 + "..."
    assert util.truncate("short") == "short"
