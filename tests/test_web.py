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
    assert b'href="./style.css"' in body
    assert b'src="./app.js"' in body
    status, body = _req(web_env["port"], "GET", "/static/style.css")
    assert status == 200 and b"--cool" in body
    status, body = _req(web_env["port"], "GET", "/style.css")
    assert status == 200 and b"deepsee" in body
    status, body = _req(web_env["port"], "GET", "/app.js")
    assert status == 200 and b"loadStatus" in body
    assert b"esc(c.provider)" in body and b"esc(c.model)" in body
    status, body = _req(web_env["port"], "GET", "/favicon.ico")
    assert status == 204 and body == b""


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


def _point_upstream(web_env, mock_upstream):
    """把 web_env 的 upstream 指向 mock 上游,避免真实网络。"""
    from deepsee.config import save

    cfg = web_env["cfg"]
    cfg["upstream"]["base_url"] = f"http://127.0.0.1:{mock_upstream['port']}/v1"
    cfg["upstream"]["api_key"] = "uk"
    save(cfg)


def test_chat_nonstream(web_env, mock_upstream):
    _point_upstream(web_env, mock_upstream)
    status, body = _req(web_env["port"], "POST", "/api/chat", {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}],
        "stream": False,
    })
    data = json.loads(body)
    assert status == 200
    assert data["text"] == "上游回复"


def test_chat_stream(web_env, mock_upstream):
    _point_upstream(web_env, mock_upstream)
    status, body = _req(web_env["port"], "POST", "/api/chat", {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert status == 200
    text = body.decode("utf-8")
    assert "你好" in text
    assert '"done": true' in text


def test_chat_requires_vision(web_env):
    from deepsee.config import save

    cfg = web_env["cfg"]
    cfg["vision"]["chain"] = []
    save(cfg)
    status, body = _req(web_env["port"], "POST", "/api/chat",
                        {"messages": [], "stream": False})
    assert status == 400


def test_chat_demo_reply_when_no_upstream_key(web_env):
    """无上游 key 时返回演示回复,界面仍可点通。"""
    from deepsee.config import save

    cfg = web_env["cfg"]
    cfg["upstream"]["api_key"] = ""
    save(cfg)
    status, body = _req(web_env["port"], "POST", "/api/chat",
                        {"messages": [{"role": "user", "content": "hi"}], "stream": False})
    assert status == 200
    assert "演示" in json.loads(body)["text"]


def test_status_includes_integration(web_env):
    status, body = _req(web_env["port"], "GET", "/api/status")
    data = json.loads(body)
    assert data["integration"]["model"] == "deepseek-v4-flash"
    assert "/v1" in data["integration"]["base_url"]


def test_static_path_traversal_blocked(web_env):
    """/static/ 目录穿越 → 404。"""
    status, _ = _req(web_env["port"], "GET", "/static/../../../../etc/passwd")
    assert status == 404


def test_put_config_forbidden_origin(web_env):
    """恶意 Origin 改写配置 → 403(CSRF 防护)。"""
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{web_env['port']}/api/config",
        data=b'{"upstream":{"base_url":"https://evil.example.com/v1"}}',
        headers={"Content-Type": "application/json",
                 "Origin": "https://evil.example.com"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status != 200  # 不应成功
    except urllib.error.HTTPError as e:
        assert e.code == 403


def test_web_rejects_non_object_json(web_env):
    status, body = _req(web_env["port"], "POST", "/api/test", [])
    assert status == 400
    assert "object" in json.loads(body)["error"]


def test_web_rejects_oversized_body(web_env):
    from deepsee.config import save

    cfg = web_env["cfg"]
    cfg["web"]["max_body_mb"] = 1
    save(cfg)
    status, body = _req(
        web_env["port"], "POST", "/api/test", {"padding": "x" * (1024 * 1024 + 1)}
    )
    assert status == 413
    assert "large" in json.loads(body)["error"].lower()
