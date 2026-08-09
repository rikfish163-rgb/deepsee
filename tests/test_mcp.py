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
        lambda self, source, mode="", question="", task="", verbose=False: ("一张猫图", "mock"),
    )
    monkeypatch.setattr(
        "deepsee.mcp_server.Seer.compare",
        lambda self, sources, question="", task="compare", verbose=False: ("两图有差异", "mock"),
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
    assert "analyze_image" in names
    assert "compare_images" in names
    assert out["result"]["ttlMs"] > 0
    assert out["result"]["cacheScope"] == "public"


def test_server_discover_modern_protocol():
    server = McpServer()
    out = _call(server, "server/discover", {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
    }})
    assert "2026-07-28" in out["result"]["supportedVersions"]
    assert out["result"]["serverInfo"]["name"] == "deepsee"
    assert out["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "deepsee"
    assert out["result"]["ttlMs"] > 0
    assert out["result"]["cacheScope"] == "public"


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


def test_tools_call_analyze_alias_and_compare(monkeypatch):
    server = _server(monkeypatch)
    out = _call(server, "tools/call", {"name": "analyze_image", "arguments": {
        "source": "/x.png", "task": "error", "question": "怎么修？",
    }})
    assert "猫图" in out["result"]["content"][0]["text"]
    out = _call(server, "tools/call", {"name": "compare_images", "arguments": {
        "sources": ["/a.png", "/b.png"], "question": "哪里不同？",
    }})
    assert "两图有差异" in out["result"]["content"][0]["text"]


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
    # MCP 规范:未知工具 → JSON-RPC -32602
    assert out["error"]["code"] == -32602
    assert "Unknown tool" in out["error"]["message"]


def test_invalid_params_type():
    """params 非对象 → -32602。"""
    server = McpServer()
    out = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []})
    assert out["error"]["code"] == -32602


def test_missing_method_is_invalid_request():
    """带 id 但缺 method → -32600 Invalid Request。"""
    server = McpServer()
    out = server.handle({"jsonrpc": "2.0", "id": 1})
    assert out["error"]["code"] == -32600


def test_initialize_negotiates_known_version():
    """回显已知协议版本,未知版本回退到服务端版本。"""
    server = McpServer()
    out = _call(server, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    assert out["result"]["protocolVersion"] == "2024-11-05"
    out2 = _call(server, "initialize", {"protocolVersion": "9999-99-99", "capabilities": {}})
    assert out2["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION


def test_serve_stdio_parse_error(monkeypatch):
    """坏 JSON 行 → -32700 Parse error,不崩溃。"""
    import io

    orig_stdin, orig_stdout = mcp_server.sys.stdin, mcp_server.sys.stdout
    try:
        mcp_server.sys.stdin = io.StringIO("this is not json\n")
        buf = io.StringIO()
        mcp_server.sys.stdout = buf
        mcp_server.serve_stdio()
        out = json.loads(buf.getvalue().strip())
    finally:
        mcp_server.sys.stdin, mcp_server.sys.stdout = orig_stdin, orig_stdout
    assert out["error"]["code"] == -32700


def test_tool_result_truncated(monkeypatch):
    """超长工具结果被截断。"""
    server = McpServer()
    cfg = {"vision": {"chain": [{"provider": "mock"}]}, "upstream": {}, "generation": {}}
    server.cfg = cfg
    monkeypatch.setattr("deepsee.mcp_server.load", lambda: cfg)

    class _FakeSeer:
        def __init__(self, *a, **k):
            pass

        @classmethod
        def from_config(cls, cfg):
            return cls()

        def describe(self, *a, **k):
            return "x" * (mcp_server.MAX_RESULT_CHARS + 1000), "mock"

    monkeypatch.setattr(mcp_server, "Seer", _FakeSeer)
    out = _call(server, "tools/call", {"name": "describe_image", "arguments": {"source": "/x.png"}})
    text = out["result"]["content"][0]["text"]
    assert len(text) <= mcp_server.MAX_RESULT_CHARS + 30
    assert "已截断" in text


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


def test_hook_json_contract(tmp_path):
    """hook 输出的 updatedInput JSON 契约(嵌套 hookSpecificOutput.updatedInput)。"""
    import io

    from deepsee import hook_claude as h
    from deepsee.config import load, save

    # 在隔离的 DEEPSEE_HOME 里写一个带视觉链的配置(hook 才不提前透传)
    cfg = load()
    cfg["vision"]["chain"] = [{"provider": "demo", "base_url": "", "api_key": "", "model": ""}]
    cfg["agents"]["claude_code"]["hook"] = True
    save(cfg)

    class _FakeSeer:
        def __init__(self, *a, **k):
            pass

        @classmethod
        def from_config(cls, cfg):
            return cls()

        def describe(self, fp):
            return "这是一张红色图片。", "demo"

    orig_seer = h.Seer
    h.Seer = _FakeSeer
    buf = io.StringIO()
    stdin = io.StringIO(json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.png"}, "model": "deepseek-v4-flash",
    }))
    old_stdin, old_stdout = h.sys.stdin, h.sys.stdout
    h.sys.stdin, h.sys.stdout = stdin, buf
    try:
        assert h.run_stdin() == 0
    finally:
        h.sys.stdin, h.sys.stdout = old_stdin, old_stdout
        h.Seer = orig_seer
    data = json.loads(buf.getvalue())
    upd = data["hookSpecificOutput"]["hookSpecificOutput"]["updatedInput"]
    assert upd["file_path"].endswith(".txt")
    assert data["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_describe_no_vision_is_error(monkeypatch):
    """未配置视觉链时 describe_image 返回 isError:true,而非假成功。"""
    server = McpServer()
    cfg = {"vision": {"chain": []}, "upstream": {}, "generation": {}}
    server.cfg = cfg
    monkeypatch.setattr("deepsee.mcp_server.load", lambda: cfg)
    out = _call(server, "tools/call", {"name": "describe_image", "arguments": {}})
    assert out["result"]["isError"] is True
    assert "视觉" in out["result"]["content"][0]["text"]
