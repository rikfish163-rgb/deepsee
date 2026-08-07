"""MCP server 协议测试(NDJSON JSON-RPC,离线)。"""

import json

import pytest

from deepsee import mcp_server
from deepsee.mcp_server import McpServer


def _server(monkeypatch):
    server = McpServer()
    cfg = {
        "vision": {"chain": [{"provider": "mock", "base_url": "x", "api_key": "k",
                              "model": "m"}]},
        "upstream": {"base_url": "x", "api_key": "", "model": "deepseek-v4-flash",
                     "format": "openai"},
        "generation": {"provider": "custom", "base_url": "", "api_key": "", "model": ""},
    }
    server.cfg = cfg
    # handle() 会重新 load();这里让 load() 返回测试配置
    monkeypatch.setattr("deepsee.mcp_server.load", lambda: cfg)
    monkeypatch.setattr(
        "deepsee.mcp_server.Seer.describe",
        lambda self, source, mode="", question="", verbose=False: ("一张猫图", "mock"),
    )
    return server


def _call(server, method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def test_initialize():
    server = McpServer()
    out = _call(server, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    assert out["id"] == 1
    assert out["result"]["serverInfo"]["name"] == "deepsee"
    assert "tools" in out["result"]["capabilities"]


def test_notification_returns_none():
    server = McpServer()
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list():
    server = McpServer()
    out = _call(server, "tools/list")
    names = [t["name"] for t in out["result"]["tools"]]
    assert "describe_image" in names
    assert "generate_image" in names
    assert "vision_status" in names


def test_tools_call_describe(monkeypatch):
    server = _server(monkeypatch)
    out = _call(server, "tools/call", {"name": "describe_image", "arguments": {"source": "/x.png"}})
    text = out["result"]["content"][0]["text"]
    assert "猫图" in text
    assert out["result"]["isError"] is False


def test_tools_call_ocr(monkeypatch):
    server = _server(monkeypatch)
    out = _call(server, "tools/call", {"name": "ocr_image", "arguments": {"source": "/x.png"}})
    assert "猫图" in out["result"]["content"][0]["text"]


def test_tools_call_status():
    server = McpServer()
    out = _call(server, "tools/call", {"name": "vision_status", "arguments": {}})
    status = json.loads(out["result"]["content"][0]["text"])
    assert "problems" in status
    assert "vision_chain" in status


def test_unknown_method_errors():
    server = McpServer()
    out = _call(server, "wat")
    assert out["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    server = McpServer()
    out = _call(server, "tools/call", {"name": "nope", "arguments": {}})
    assert out["result"]["isError"] is True


def test_serve_stdio_roundtrip(monkeypatch, capsys):
    """模拟 stdin 两行 → 校验 stdout 换行分隔 JSON-RPC。"""
    import io

    server = _server(monkeypatch)
    lines_in = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    captured = []
    orig_stdin, orig_stdout = mcp_server.sys.stdin, mcp_server.sys.stdout
    try:
        mcp_server.sys.stdin = io.StringIO("\n".join(lines_in) + "\n")
        buf = io.StringIO()
        mcp_server.sys.stdout = buf
        mcp_server.serve_stdio()
        captured = buf.getvalue().strip().split("\n")
    finally:
        mcp_server.sys.stdin, mcp_server.sys.stdout = orig_stdin, orig_stdout
    assert len(captured) == 2
    assert json.loads(captured[0])["id"] == 1
    assert json.loads(captured[1])["result"]["tools"][0]["name"]
