"""双协议代理集成测试:起真实代理进程,打真实 HTTP(mock 上游/视觉)。"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from deepsee.proxy import ProxyHandler, run_proxy


def _start_proxy(cfg):
    from http.server import ThreadingHTTPServer

    srv = run_proxy("127.0.0.1", 0, cfg)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def _post(port, path, body: dict, key: str = "") -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _get(port, path) -> tuple[int, bytes]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.status, r.read()


def _cfg(upstream_port, vision_port, fmt="openai", master=""):
    return {
        "vision": {"chain": [
            {"provider": "mock", "base_url": f"http://127.0.0.1:{vision_port}/v1",
             "api_key": "vk", "model": "mock-vlm"},
        ], "mode": "describe", "max_tokens": 256, "timeout": 20},
        "upstream": {"base_url": f"http://127.0.0.1:{upstream_port}/v1",
                     "api_key": "uk", "model": "deepseek-v4-flash", "format": fmt},
        "proxy": {"port": 0, "master_key": master},
        "generation": {"provider": "custom", "base_url": "", "api_key": "", "model": ""},
        "web": {"port": 0},
    }


def test_openai_chat_image_replaced(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"]))
    try:
        body = {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "帮我看看这张图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ]},
            ],
        }
        status, resp = _post(port, "/v1/chat/completions", body)
        assert status == 200
        data = json.loads(resp)
        assert data["choices"][0]["message"]["content"] == "上游回复"

        # 上游收到的请求已无 image_url,且带 [Image 1] 描述
        sent = json.loads(mock_upstream["rec"].requests[-1])
        content = sent["messages"][0]["content"]
        assert all(c["type"] != "image_url" for c in content)
        assert any("[Image 1]" in c.get("text", "") for c in content)
        assert "【视觉描述】" in "".join(c.get("text", "") for c in content)
        # 视觉 API 收到过一张图
        v_sent = json.loads(mock_vision["rec"].requests[-1])
        assert v_sent["messages"][0]["content"][0]["type"] == "image_url"
    finally:
        srv.shutdown(); srv.server_close()


def test_openai_stream_passthrough(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"]))
    try:
        body = {
            "model": "m", "stream": True,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]}],
        }
        status, resp = _post(port, "/v1/chat/completions", body)
        assert status == 200
        text = resp.decode("utf-8")
        assert "data:" in text and "[DONE]" in text and "你好" in text
    finally:
        srv.shutdown(); srv.server_close()


def test_anthropic_to_openai_cross_protocol(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"], fmt="openai"))
    try:
        body = {
            "model": "deepseek-v4-flash",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "看一下"},
                {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": "QUFBQQ=="}},
            ]}],
        }
        status, resp = _post(port, "/v1/messages", body)
        assert status == 200
        data = json.loads(resp)
        assert data["type"] == "message"
        assert data["content"][0]["type"] == "text"
        assert "上游回复" in data["content"][0]["text"]
        sent = json.loads(mock_upstream["rec"].requests[-1])
        content = sent["messages"][0]["content"]
        assert isinstance(content, str) and "[Image 1]" in content
    finally:
        srv.shutdown(); srv.server_close()


def test_anthropic_same_protocol(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"], fmt="anthropic"))
    try:
        body = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": "QUFBQQ=="}},
            ]}],
        }
        status, resp = _post(port, "/v1/messages", body)
        assert status == 200
        data = json.loads(resp)
        assert data["choices"][0]["message"]["content"] == "上游回复"  # mock 原样透传
        sent = json.loads(mock_upstream["rec"].requests[-1])
        blocks = sent["messages"][0]["content"]
        assert all(b.get("type") != "image" for b in blocks)
        assert "[Image 1]" in "".join(b.get("text", "") for b in blocks)
    finally:
        srv.shutdown(); srv.server_close()


def test_auth_required(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"], master="secret"))
    try:
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        status, _ = _post(port, "/v1/chat/completions", body)  # 无 key
        assert status == 401
        status, _ = _post(port, "/v1/chat/completions", body, key="secret")
        assert status == 200
    finally:
        srv.shutdown(); srv.server_close()


def test_models_and_health(mock_vision, mock_upstream):
    srv, port = _start_proxy(_cfg(mock_upstream["port"], mock_vision["port"]))
    try:
        status, resp = _get(port, "/v1/models")
        assert status == 200
        assert json.loads(resp)["data"][0]["id"] == "deepseek-v4-flash"
        status, _ = _get(port, "/health")
        assert status == 200
    finally:
        srv.shutdown(); srv.server_close()


def test_anthropic_base_dedup_v1():
    """_anthropic_base 应避免 /v1/v1 双路径。"""
    from deepsee.proxy import _anthropic_base

    # 通用中继: OpenAI base 带 /v1,Anthropic 应去 /v1
    assert _anthropic_base({"base_url": "https://relay.example.com/v1"}) == "https://relay.example.com"
    # 显式 anthropic_base_url 优先
    assert _anthropic_base({"base_url": "https://relay.example.com/v1",
                            "anthropic_base_url": "https://relay.example.com/anthropic"}) == "https://relay.example.com/anthropic"
    # DeepSeek 官方特例
    assert _anthropic_base({"base_url": "https://api.deepseek.com/v1"}) == "https://api.deepseek.com/anthropic"
    # 无 /v1 尾原样
    assert _anthropic_base({"base_url": "https://x.com"}) == "https://x.com"
