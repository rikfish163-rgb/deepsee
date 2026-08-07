"""手写轻量 MCP Server(stdio JSON-RPC 2.0,零依赖)。

协议:MCP stdio 走换行分隔的 JSON-RPC(与官方 SDK 一致)。
实现:initialize / ping / tools/list / tools/call;未声明的 capability
(resources/prompts/completions)返回空结果而非报错;通知类忽略。
工具:describe_image(see) / ocr_image / generate_image / vision_status。
仅标准库,零第三方依赖。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .caption import Seer
from .config import load, validate, vision_chain
from .generate import ImageGenClient

TOOLS: list[dict] = [
    {
        "name": "describe_image",
        "description": "识别图片(本地路径 / URL / 剪贴板留空),返回详细文字描述。DeepSeek 等纯文本模型用它'看'图。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "图片路径或 http(s) URL;留空则用最近截图/剪贴板"},
                "mode": {"type": "string", "enum": ["describe", "ocr", "qa"], "default": "describe"},
                "question": {"type": "string", "description": "mode=qa 时的问题"},
            },
            "required": [],
        },
    },
    {
        "name": "ocr_image",
        "description": "提取图片中的文字(OCR)。参数 source: 路径/URL/留空=剪贴板。",
        "inputSchema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "generate_image",
        "description": "根据提示词生成图片并保存到本地,返回保存路径。",
        "inputSchema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}, "size": {"type": "string"}},
            "required": ["prompt"],
        },
    },
    {
        "name": "vision_status",
        "description": "查看 deepsee 配置与视觉链路状态。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

SERVER_INFO = {"name": "deepsee", "version": __version__}
PROTOCOL_VERSION = "2025-06-18"
_KNOWN_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
CAPABILITIES = {"tools": {"listChanged": False}}
# 单次工具结果上限(避免撑爆客户端 tool result 缓冲)
MAX_RESULT_CHARS = 200_000
# 工具名白名单(含 see 别名)
_KNOWN_TOOLS = {"describe_image", "see", "ocr_image", "generate_image", "vision_status"}


class McpServer:
    def __init__(self):
        self.cfg = load()
        self._seer_cache = None
        self._seer_mtime = None

    # -- 请求处理 ---------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        if not isinstance(msg, dict):
            return None
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            return _error(msg.get("id"), -32600, "Invalid Request")
        params = msg.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(msg.get("id"), -32602, "params must be an object")
        params = params or {}
        msg_id = msg.get("id")

        self.cfg = load()  # mtime 缓存,便宜;反映外部配置改动

        if method == "initialize":
            # 只回显已知协议版本,未知则回退到服务端实际实现的版本
            pv = params.get("protocolVersion")
            if pv not in _KNOWN_PROTOCOL_VERSIONS:
                pv = PROTOCOL_VERSION
            return _response(msg_id, {
                "protocolVersion": pv,
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "deepsee:让 DeepSeek 等纯文本模型'看图'。需要理解图片时调用 "
                    "describe_image(路径/URL/留空=最近截图),提取文字用 ocr_image。"
                ),
            })
        if method == "ping":
            return _response(msg_id, {})
        if method == "tools/list":
            return _response(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._tool_call(msg_id, params)
        # 未声明的 capability,给空结果而非报错(部分客户端会探测)
        if method == "resources/list":
            return _response(msg_id, {"resources": []})
        if method == "resources/templates/list":
            return _response(msg_id, {"resourceTemplates": []})
        if method == "prompts/list":
            return _response(msg_id, {"prompts": []})
        if method == "completions/complete":
            return _response(msg_id, {"completion": {"values": [], "total": 0}})
        if method == "resources/read":
            return _error(msg_id, -32602, "no resources available")
        if method == "prompts/get":
            return _error(msg_id, -32602, "no prompts available")
        # 通知类:忽略(不响应)
        if method.startswith("notifications/"):
            return None
        if msg_id is not None:
            return _error(msg_id, -32601, f"method not found: {method}")
        return None

    def _tool_call(self, msg_id, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in _KNOWN_TOOLS:
            # MCP 规范:未知工具返回 JSON-RPC -32602
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _response(msg_id, {
                "content": [{"type": "text", "text": "错误: arguments 必须是对象"}],
                "isError": True,
            })
        try:
            text = self._run(name, args)
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS] + "\n…(结果过长已截断)"
            return _response(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # noqa: BLE001
            return _response(msg_id, {
                "content": [{"type": "text", "text": f"错误: {e}"}], "isError": True,
            })

    def _run(self, name: str, args: dict) -> str:
        if name in ("describe_image", "see"):
            return self._describe(args.get("source", ""), args.get("mode", ""), args.get("question", ""))
        if name == "ocr_image":
            return self._describe(args.get("source", ""), "ocr", "")
        if name == "generate_image":
            return self._generate(args)
        if name == "vision_status":
            return self._status()
        raise ValueError(f"未知工具: {name}")

    # -- 工具实现 ---------------------------------------------------------
    def _seer(self) -> Seer:
        import os

        from .config import config_path

        try:
            mtime = os.stat(config_path()).st_mtime_ns
        except OSError:
            mtime = None
        if self._seer_cache is None or self._seer_mtime != mtime:
            self._seer_cache = Seer.from_config(self.cfg)
            self._seer_mtime = mtime
        return self._seer_cache

    def _describe(self, source: str, mode: str, question: str) -> str:
        if not self.cfg.get("vision", {}).get("chain"):
            raise ValueError("未配置视觉 API。请先运行 `deepsee setup` 或编辑 ~/.deepsee/config.json。")
        seer = self._seer()
        text, provider = seer.describe(source, mode=mode, question=question)
        return f"[provider: {provider}]\n{text}"

    def _generate(self, args: dict) -> str:
        gen = self.cfg.get("generation", {})
        if not gen.get("api_key") or not gen.get("base_url"):
            raise ValueError("未配置文生图后端。请先运行 `deepsee setup` 或在配置页填写。")
        client = ImageGenClient.from_config(self.cfg, size=args.get("size", ""))
        path = client.generate(args.get("prompt", ""))
        return f"图片已保存: {path}"

    def _status(self) -> str:
        problems = validate(self.cfg)
        chain = vision_chain(self.cfg)
        info = {
            "vision_chain": [{"provider": c.get("provider"), "model": c.get("model")} for c in chain],
            "problems": problems,
            "ok": not problems,
            "upstream_model": self.cfg.get("upstream", {}).get("model"),
        }
        return json.dumps(info, ensure_ascii=False, indent=2)


def _response(msg_id, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write_line(obj: dict) -> bool:
    """写一行 JSON-RPC 响应;客户端断开时返回 False 干净退出。"""
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def serve_stdio() -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # 某些环境不支持 reconfigure
    server = McpServer()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            if not _write_line({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": "Parse error"}}):
                return 0
            continue
        if not isinstance(msg, dict):
            if not _write_line({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32600, "message": "Invalid Request"}}):
                return 0
            continue
        try:
            out = server.handle(msg)
        except Exception as e:  # noqa: BLE001
            if msg.get("id") is not None:
                out = _error(msg["id"], -32603, str(e))
            else:
                out = None
        if out is not None:
            if not _write_line(out):
                return 0
    return 0


def main() -> int:
    return serve_stdio()
