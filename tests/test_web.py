"""Web 面板测试:状态/配置掩码/识图/文生图路由(离线,mock 视觉)。"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from deepsee.config import save
from deepsee.web import WebHandler


@pytest.fixture
def web_env(tmp_path, monkeypatch, mock_vision):
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path))
    cfg = {
        "vision": {"chain": [
            {"provider": "mock", "base_url": f"http://127.0.0.1:{mock_vision['port']}/v1",
             "api_key": "sk-livesecret", "model": "mock-vlm"},
        ], "mode": "describe"},
        "upstream": {"base_url": "https://api.deepseek.com/v1", "api_key": "uk",
                     "model": "deepseek-v4-flash", "format": "openai"},
        "generation": {"provider": "custom", "base_url": "", "api_key": "", "model": ""},
        "proxy": {"port": 8080, "master_key": ""},
        "web": {"port": 8090},
        "agents": {"claude_code": {"hook": False}, "codex": {"skill": False},
                   "opencode": {"patched": False}},
    }
    save(cfg)
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"port": srv.server_address[1], "cfg": cfg}
    srv.shutdown()
    srv.server_close()


def _req(port, method, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_index_served(web_env):
    status, body = _req(web_env["port"], "GET", "/")
    assert status == 200
    assert b"deepsee" in body
    status, body = _req(web_env["port"], "GET", "/static/style.css")
    assert status == 200 and b"--cool" in body


def test_status_api(web_env):
    status, body = _req(web_env["port"], "GET", "/api/status")
    data = json.loads(body)
    assert data["version"]
    assert data["vision"]["chain"][0]["provider"] == "mock"
    assert data["upstream"]["model"] == "deepseek-v4-flash"


def test_config_masked(web_env):
    status, body = _req(web_env["port"], "GET", "/api/config")
    data = json.loads(body)
    assert "·" in data["vision"]["chain"][0]["api_key"]


def test_config_put_masked_key_keeps_old(web_env):
    patch = {"vision": {"mode": "ocr",
                        "chain": [{"provider": "mock", "model": "mock-vlm",
                                   "api_key": "····secret"}]}}
    status, body = _req(web_env["port"], "PUT", "/api/config", patch)
    data = json.loads(body)
    assert data["vision"]["mode"] == "ocr"
    # 掩码的 key 应被真实 key 顶替:响应是真实 key(sk-livesecret)的掩码形态
    assert data["vision"]["chain"][0]["api_key"] == "·" * 9 + "cret"
    # 落盘的是真实 key
    from deepsee.config import load

    saved = load()
    assert saved["vision"]["chain"][0]["api_key"] == "sk-livesecret"


def test_see_with_base64(web_env, mock_vision):
    import base64

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x").decode()
    status, body = _req(web_env["port"], "POST", "/api/test",
                        {"base64": "data:image/png;base64," + png, "mode": "describe"})
    data = json.loads(body)
    assert status == 200
    assert "视觉描述" in data["text"]
    assert data["provider"] == "mock"
    assert data["latency_ms"] >= 0


def test_see_without_chain(web_env):
    from deepsee.config import save

    cfg = web_env["cfg"]
    cfg["vision"]["chain"] = []
    save(cfg)
    status, body = _req(web_env["port"], "POST", "/api/test", {"source": "/x.png"})
    assert status == 400


def test_gen_not_configured(web_env):
    status, body = _req(web_env["port"], "POST", "/api/gen", {"prompt": "猫"})
    data = json.loads(body)
    assert status == 400
    assert "未配置" in data["error"]
