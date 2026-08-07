"""Web 面板:stdlib http.server + 单页前端(状态/识图台/画图台/配置)。"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .caption import Seer
from .config import deep_merge, load, save, validate, vision_chain
from .util import cprint, latest_session_image, mask_secrets

STATIC_DIR = Path(__file__).parent / "web" / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# 静态文件字节缓存(启动后只读一次)
_STATIC_CACHE: dict[str, tuple[bytes, str] | None] = {}


def _static(rel: str):
    if rel not in _STATIC_CACHE:
        f = STATIC_DIR / rel
        if f.is_file():
            _STATIC_CACHE[rel] = (f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
        else:
            _STATIC_CACHE[rel] = None
    return _STATIC_CACHE[rel]


class WebHandler(BaseHTTPRequestHandler):
    server_version = "deepsee-web/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_OPTIONS(self):
        self._send(200, b"", "text/plain")

    # -- GET --------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, _static("index.html")[0], MIME[".html"])
        if path.startswith("/static/"):
            rel = path[len("/static/"):].replace("..", "")
            cached = _static(rel)
            if cached:
                body, ctype = cached
                return self._send(200, body, ctype, {"Cache-Control": "max-age=300"})
            return self._json(404, {"error": "not found"})
        if path == "/api/status":
            return self._json(200, _status_payload())
        if path == "/api/config":
            return self._json(200, _masked_config())
        if path == "/api/health":
            return self._json(200, {"ok": True, "version": __version__})
        if path == "/static_out":
            return self._serve_generated()
        return self._json(404, {"error": "not found"})

    def _serve_generated(self) -> None:
        """服务文生图输出目录里的图片(仅限 basename,防目录穿越)。"""
        from urllib.parse import parse_qs, urlparse

        from .generate import _default_save_dir

        name = parse_qs(urlparse(self.path).query).get("p", [""])[0]
        if not name or "/" in name or "\\" in name:
            return self._json(404, {"error": "not found"})
        f = _default_save_dir() / name
        if not f.is_file():
            return self._json(404, {"error": "not found"})
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}.get(f.suffix.lower(), "image/png")
        return self._send(200, f.read_bytes(), mime)

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        path = self.path.split("?")[0]
        raw = self._read_body()
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        if path == "/api/test":
            return self._api_test(body)
        if path == "/api/gen":
            return self._api_gen(body)
        if path == "/api/chat":
            return self._api_chat(body)
        if path == "/api/latest":
            p = latest_session_image()
            if not p:
                return self._json(404, {"error": "no image found in session logs"})
            return self._json(200, {"path": str(p)})
        return self._json(404, {"error": "not found"})

    # -- PUT --------------------------------------------------------------
    def do_PUT(self):
        path = self.path.split("?")[0]
        raw = self._read_body()
        if path != "/api/config":
            return self._json(404, {"error": "not found"})
        try:
            patch = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        cfg = load()
        new_cfg = deep_merge(cfg, _restore_masked_keys(cfg, patch))
        save(new_cfg)
        return self._json(200, _masked_config())

    # -- API --------------------------------------------------------------
    def _api_test(self, body: dict):
        cfg = load()
        seer = Seer.from_config(cfg)
        if not cfg.get("vision", {}).get("chain"):
            return self._json(400, {"error": "未配置视觉 API,请先到配置页填写"})
        source = body.get("source", "") or ""
        mode = body.get("mode", "")
        question = body.get("question", "")
        t0 = time.time()
        try:
            if body.get("base64") and body["base64"].startswith("data:image"):
                import base64

                text, provider = seer.describe_bytes(
                    base64.b64decode(body["base64"].split(",", 1)[1]),
                    mode=mode, question=question)
            else:
                text, provider = seer.describe(source, mode=mode, question=question)
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": str(e)})
        return self._json(200, {"text": text, "provider": provider,
                                "latency_ms": int((time.time() - t0) * 1000)})

    def _api_gen(self, body: dict):
        from .generate import GenError, ImageGenClient

        cfg = load()
        gen = cfg.get("generation", {})
        if not gen.get("api_key") or not gen.get("base_url"):
            return self._json(400, {"error": "文生图未配置"})
        client = ImageGenClient.from_config(cfg, size=body.get("size", ""))
        try:
            path = client.generate(body.get("prompt", ""))
        except GenError as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, {"path": str(path), "url": f"/static_out?p={path.name}"})

    def _api_chat(self, body: dict):
        """对话:图片自动转文字后转发 DeepSeek。stream=true 走 SSE 流式。"""
        from .chat import ChatError, chat_openai, iter_chat

        cfg = load()
        if not cfg.get("vision", {}).get("chain"):
            return self._json(400, {"error": "未配置视觉 API,请先到配置页填写"})
        stream = bool(body.get("stream"))
        messages = body.get("messages", [])
        payload = {
            "model": cfg["upstream"].get("model", ""),
            "messages": messages,
            "max_tokens": 4096,
        }
        # 无上游 key → 演示回复,让对话界面零 key 也能点通
        if not cfg.get("upstream", {}).get("api_key"):
            demo = (
                "【演示】上游 DeepSeek 尚未配置 api_key,暂无法生成真实回复。\n"
                "在「配置」页填入 DeepSeek 的 api_key 保存后即可对话;"
                "对话里贴的图片会自动转成文字再交给 DeepSeek。"
            )
            if not stream:
                return self._json(200, {"text": demo})
            self._begin_sse()
            try:
                self._sse({"delta": demo})
                self._sse({"done": True})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if not stream:
            try:
                text = chat_openai(cfg, payload)
            except ChatError as e:
                return self._json(502, {"error": str(e)})
            return self._json(200, {"text": text})

        # SSE 流式
        self._begin_sse()
        try:
            for delta in iter_chat(cfg, payload):
                self._sse({"delta": delta})
            self._sse({"done": True})
        except ChatError as e:
            self._sse({"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._sse({"error": f"internal: {e}"})
        finally:
            try:
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _begin_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

    def _sse(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


def _masked_config() -> dict:
    cfg = deepcopy(load())
    mask_secrets(cfg)
    return cfg


def _restore_masked_keys(cfg, patch):
    """保存配置时:patch 中掩码的 api_key(含·)用旧配置里同路径的真实 key 顶替。

    按字典键 / 列表 index 与 cfg 对齐,避免掩码覆盖真实 key。
    """
    if not isinstance(patch, dict):
        return patch
    out: dict = {}
    for k, v in patch.items():
        if k == "api_key" and isinstance(v, str) and "·" in v:
            out[k] = cfg.get(k) if isinstance(cfg, dict) and cfg.get(k) else ""
        elif isinstance(v, dict) and isinstance(cfg, dict) and isinstance(cfg.get(k), dict):
            out[k] = _restore_masked_keys(cfg[k], v)
        elif isinstance(v, list):
            old_list = cfg.get(k) if isinstance(cfg, dict) and isinstance(cfg.get(k), list) else []
            out[k] = []
            for i, item in enumerate(v):
                old_item = old_list[i] if i < len(old_list) and isinstance(old_list[i], dict) else {}
                out[k].append(_restore_masked_keys(old_item, item) if isinstance(item, dict) else item)
        else:
            out[k] = v
    return out


def _status_payload() -> dict:
    from .installer import detect_agents

    cfg = load()
    chain = vision_chain(cfg)
    return {
        "version": __version__,
        "vision": {
            "chain": [{"provider": c.get("provider"), "model": c.get("model"),
                       "key_set": bool(c.get("api_key"))} for c in chain],
            "mode": cfg["vision"].get("mode"),
        },
        "upstream": {"base_url": cfg["upstream"].get("base_url"),
                     "model": cfg["upstream"].get("model"),
                     "key_set": bool(cfg["upstream"].get("api_key"))},
        "generation": {"model": cfg["generation"].get("model"),
                       "key_set": bool(cfg["generation"].get("api_key"))},
        "agents": detect_agents(),
        "problems": validate(cfg),
        "ports": {"proxy": cfg["proxy"].get("port"), "web": cfg["web"].get("port")},
        "integration": {
            "base_url": f"http://127.0.0.1:{cfg['proxy'].get('port', 8080)}/v1",
            "model": cfg["upstream"].get("model", ""),
            "master_key": cfg["proxy"].get("master_key", ""),
        },
    }


def serve_forever(host: str, port: int, cfg: dict = None):
    try:
        server = ThreadingHTTPServer((host, int(port)), WebHandler)
    except OSError as e:
        if e.errno == 98:  # EADDRINUSE
            cprint(f"  端口 {port} 已被占用,换一个: deepsee web -p <新端口>", "red", bold=True)
        else:
            cprint(f"  启动失败: {e}", "red")
        return 1
    cprint(f"\n  deepsee Web 面板已启动 → http://{host}:{port}", "green", bold=True)
    cprint("  按 Ctrl+C 停止", "cyan")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cprint("\n  已停止", "yellow")
    finally:
        server.server_close()
    return 0
