"""pytest 共享 fixtures:本地 mock 视觉/上游服务器(全部离线)。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture(autouse=True)
def _isolate_deepsee_home(tmp_path, monkeypatch):
    """每个测试都用独立的临时配置目录,绝不读写真实 ~/.deepsee;并关闭 HTTP 连接池。"""
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path / ".deepsee"))
    yield
    from deepsee import util

    util.close_http_pool()


class Recorder:
    """记录收到的最后一个请求体,便于断言。"""

    def __init__(self):
        self.requests: list[dict] = []


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _respond(self, obj: object):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        self.server.recorder.requests.append(raw)

        if self.server.mode == "vision":
            return self._respond({
                "choices": [{"message": {"role": "assistant",
                                         "content": "【视觉描述】图片里有一只柴犬在打篮球。"}}]
            })
        if self.server.mode == "upstream":
            if json.loads(raw.decode("utf-8", "replace")).get("stream"):
                chunks = [
                    'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n',
                    'data: {"choices":[{"delta":{"content":"你好"},"index":0}]}\n\n',
                    "data: [DONE]\n\n",
                ]
                body = "".join(chunks).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return self._respond({
                "id": "cmpl-mock", "object": "chat.completion",
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": "上游回复"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            })
        return self._respond({"error": "unknown mode"})


class MockServer(ThreadingHTTPServer):
    def __init__(self, mode: str, recorder: Recorder):
        super().__init__(("127.0.0.1", 0), MockHandler)
        self.mode = mode
        self.recorder = recorder


def _spawn(mode: str):
    rec = Recorder()
    srv = MockServer(mode, rec)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return rec, srv


@pytest.fixture
def mock_vision():
    rec, srv = _spawn("vision")
    yield {"port": srv.server_address[1], "rec": rec}
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def mock_upstream():
    rec, srv = _spawn("upstream")
    yield {"port": srv.server_address[1], "rec": rec}
    srv.shutdown()
    srv.server_close()
