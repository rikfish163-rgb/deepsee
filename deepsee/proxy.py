"""双协议代理:OpenAI /v1/chat/completions + Anthropic /v1/messages。

把请求中的图片块替换成文字描述,再转发到 DeepSeek(纯文本)上游。
- 同协议:流式透传(只改请求,响应 SSE 原样转发)
- 跨协议:仅非流式(格式转换)
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .caption import Seer
from .chat import Converter, build_openai_payload
from .util import HttpError, cprint, http_json, http_stream, safe_join


class ProxyError(Exception):
    def __init__(self, status: int, msg: str):
        self.status = status
        self.msg = msg
        super().__init__(msg)


def _anthropic_base(up: dict) -> str:
    """Anthropic 上游的基础地址:优先 anthropic_base_url。

    兜底推导:
    - DeepSeek 官方: OpenAI base 是 .../v1,Anthropic base 是 .../anthropic(特例)
    - 通用 OpenAI 兼容中继: OpenAPI base 是 {root}/v1,Anthropic 在 {root} → 去 /v1
    - 其余原样
    """
    ab = up.get("anthropic_base_url")
    if ab:
        return ab.rstrip("/")
    base = up.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    if "api.deepseek.com" in base:
        return "https://api.deepseek.com/anthropic"
    if base.endswith("/v1"):
        return base[:-3]
    return base


# ---------------------------------------------------------------------------
# 请求处理
# ---------------------------------------------------------------------------


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "deepsee/0.1"
    protocol_version = "HTTP/1.1"

    cfg: dict = {}
    seer: Converter | None = None

    # -- 基础 -----------------------------------------------------------
    def log_message(self, fmt, *args):  # 精简日志
        cprint(f"[proxy] {self.address_string()} {fmt % args}", "cyan")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _check_auth(self) -> None:
        master = (self.cfg.get("proxy") or {}).get("master_key", "")
        if not master:
            return
        auth = self.headers.get("Authorization", "")
        xkey = self.headers.get("x-api-key", "")
        if auth == f"Bearer {master}" or xkey == master:
            return
        raise ProxyError(401, "invalid or missing api key")

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(200, b"", "text/plain")

    def do_GET(self):
        try:
            self._check_auth()
            path = self.path.split("?")[0]
            if path == "/health":
                return self._send_json(200, {"ok": True, "service": "deepsee-proxy"})
            if path == "/v1/models":
                up = self.cfg.get("upstream", {})
                return self._send_json(
                    200, {"object": "list", "data": [{"id": up.get("model", ""), "object": "model"}]}
                )
            return self._send_json(200, {"service": "deepsee-proxy",
                                          "endpoints": ["/v1/chat/completions", "/v1/messages",
                                                        "/v1/images/generations", "/health"]})
        except ProxyError as e:
            return self._send_json(e.status, {"error": {"message": e.msg}})

    # -- POST 路由 -------------------------------------------------------
    def do_POST(self):
        try:
            self._check_auth()
            raw = self._read_body()
            path = self.path.split("?")[0]
            if not raw:
                raise ProxyError(400, "empty body")
            payload = json.loads(raw.decode("utf-8", "replace"))
            if path == "/v1/chat/completions":
                return self._openai_chat(payload)
            if path == "/v1/messages":
                return self._anthropic_messages(payload)
            if path == "/v1/images/generations":
                return self._image_generate(payload)
            raise ProxyError(404, f"unknown endpoint: {path}")
        except (ProxyError, ValueError) as e:
            status = e.status if isinstance(e, ProxyError) else 400
            self._send_json(status, {"error": {"message": str(e)}})
        except Exception as e:  # noqa: BLE001 兜底,不崩进程
            self._send_json(500, {"error": {"message": f"internal: {e}"}})

    # -- OpenAI ----------------------------------------------------------
    def _openai_chat(self, payload: dict):
        up = self.cfg.get("upstream", {})
        if up.get("format", "openai") == "anthropic":
            return self._openai_to_anthropic(payload)
        payload = build_openai_payload(self.cfg, payload)  # 图片 → 文字
        url = safe_join(up.get("base_url", "https://api.deepseek.com/v1"), "chat/completions")
        headers = {"Authorization": f"Bearer {up.get('api_key', '')}"}
        if payload.get("stream"):
            return self._relay_stream(url, payload, headers)
        self._forward_json(url, payload, headers)

    def _anthropic_messages(self, payload: dict):
        up = self.cfg.get("upstream", {})
        if up.get("format", "openai") == "openai":
            return self._anthropic_to_openai(payload)
        converter = self._seer()
        # Anthropic 的图片块在 messages[].content 里
        for m in payload.get("messages", []):
            content = m.get("content")
            if isinstance(content, list):
                m["content"], _n = converter.anthropic_content(content)
        url = safe_join(_anthropic_base(up), "v1/messages")
        headers = {"Authorization": f"Bearer {up.get('api_key', '')}",
                   "anthropic-version": self.headers.get("anthropic-version", "2023-06-01"),
                   "x-api-key": up.get("api_key", "")}
        if payload.get("stream"):
            return self._relay_stream(url, payload, headers)
        self._forward_json(url, payload, headers)

    def _image_generate(self, payload: dict):
        from .generate import GenError, ImageGenClient

        try:
            client = ImageGenClient.from_config(self.cfg, size=payload.get("size", ""))
            path = client.generate(payload.get("prompt", ""))
            return self._send_json(200, {"data": [{"url": path.as_uri()}], "local_path": str(path)})
        except GenError as e:
            raise ProxyError(502, str(e)) from e

    # -- 转发 ------------------------------------------------------------
    def _seer(self) -> Converter:
        if self.seer is None:
            self.seer = Converter(Seer.from_config(self.cfg))
        return self.seer

    def _post_json(self, url: str, payload: dict, headers: dict, timeout: int = 120):
        """上游 POST,统一错误包装,返回 (status, data)。"""
        try:
            status, data = http_json(url, payload, headers, timeout=timeout)
        except HttpError as e:
            raise ProxyError(502 if e.status else 503, f"upstream {e}") from e
        if status >= 400:
            raise ProxyError(status, f"upstream HTTP {status}: {str(data)[:300]}")
        return status, data

    def _forward_json(self, url: str, payload: dict, headers: dict):
        status, data = self._post_json(url, payload, headers)
        self._send_json(status, data)

    def _relay_stream(self, url: str, payload: dict, headers: dict):
        """把上游 SSE 原样转发给客户端(同协议流式透传)。"""
        try:
            gen = http_stream(url, payload, headers, timeout=120)
        except HttpError as e:
            raise ProxyError(502 if e.status else 503, f"upstream {e}") from e
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True  # 流式响应结束后关闭,避免客户端挂起
        try:
            for line in gen:
                self.wfile.write(line.encode("utf-8", "replace"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.wfile.flush()

    # -- 跨协议(非流式) ---------------------------------------------------
    def _openai_to_anthropic(self, payload: dict):
        if payload.get("stream"):
            raise ProxyError(501, "跨协议流式暂不支持,请关闭 stream")
        converter = self._seer()
        messages = payload.get("messages", [])
        new_messages, _ = converter.openai_messages(messages)
        system = ""
        for m in new_messages:
            if m.get("role") == "system":
                system = (system + "\n" if system else "") + _as_text(m.get("content"))
        ap = {"model": payload.get("model", ""),
              "max_tokens": payload.get("max_tokens", 4096),
              "messages": [{"role": m.get("role", "user"),
                            "content": [{"type": "text", "text": _as_text(m.get("content"))}]}
                           for m in new_messages if m.get("role") != "system"]}
        if system:
            ap["system"] = system
        up = self.cfg.get("upstream", {})
        url = safe_join(_anthropic_base(up), "v1/messages")
        headers = {"Authorization": f"Bearer {up.get('api_key', '')}",
                   "x-api-key": up.get("api_key", ""),
                   "anthropic-version": "2023-06-01"}
        status, data = self._post_json(url, ap, headers)
        text = ""
        try:
            for b in data.get("content", []):
                if b.get("type") == "text":
                    text += b.get("text", "")
        except AttributeError:
            pass
        self._send_json(200, {"id": data.get("id", "chatcmpl-" + str(len(text))),
                              "object": "chat.completion",
                              "choices": [{"index": 0, "message": {"role": "assistant",
                                          "content": text}, "finish_reason": "stop"}],
                              "usage": data.get("usage", {})})

    def _anthropic_to_openai(self, payload: dict):
        if payload.get("stream"):
            raise ProxyError(501, "跨协议流式暂不支持,请关闭 stream")
        converter = self._seer()
        messages: list = []
        system = ""
        sys_field = payload.get("system")
        if isinstance(sys_field, str):
            system = sys_field
        elif isinstance(sys_field, list):
            system = "".join(b.get("text", "") for b in sys_field if isinstance(b, dict))
        for m in payload.get("messages", []):
            content = m.get("content")
            if isinstance(content, list):
                content, _ = converter.anthropic_content(content)
            text = "".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            messages.append({"role": m.get("role", "user"), "content": text})
        if system:
            messages.insert(0, {"role": "system", "content": system})
        op = {"model": payload.get("model", ""), "messages": messages,
              "max_tokens": payload.get("max_tokens", 4096)}
        up = self.cfg.get("upstream", {})
        url = safe_join(up.get("base_url", "https://api.deepseek.com/v1"), "chat/completions")
        headers = {"Authorization": f"Bearer {up.get('api_key', '')}"}
        status, data = self._post_json(url, op, headers)
        self._send_json(200, {"id": "msg_" + str(len(text)), "type": "message",
                              "role": "assistant", "model": data.get("model", ""),
                              "content": [{"type": "text",
                                           "text": data["choices"][0]["message"]["content"] or ""}],
                              "usage": data.get("usage", {})})


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    parts.append("[image]")
        return "".join(parts)
    return ""


def run_proxy(host: str, port: int, cfg: dict) -> ThreadingHTTPServer:
    ProxyHandler.cfg = cfg
    ProxyHandler.seer = None
    server = ThreadingHTTPServer((host, port), ProxyHandler)
    return server


def serve_forever(host: str, port: int, cfg: dict):
    try:
        server = run_proxy(host, port, cfg)
    except OSError as e:
        if e.errno == 98:  # EADDRINUSE
            cprint(f"  端口 {port} 已被占用,换一个: deepsee start -p <新端口>", "red", bold=True)
        else:
            cprint(f"  启动失败: {e}", "red")
        return 1
    cprint(f"  deepsee 代理已启动 → http://{host}:{port}", "green", bold=True)
    cprint("  支持: POST /v1/chat/completions (OpenAI), POST /v1/messages (Anthropic)", "cyan")
    cprint("  按 Ctrl+C 停止", "cyan")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cprint("\n  已停止", "yellow")
    finally:
        server.server_close()
    return 0
