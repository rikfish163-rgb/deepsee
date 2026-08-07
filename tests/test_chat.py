"""对话服务测试(离线,mock 视觉/上游)。"""

import json

import pytest

from deepsee import chat
from deepsee.util import HttpError

IMG = "data:image/png;base64,QUFBQQ=="


def _cfg(upstream_port, vision_port):
    return {
        "vision": {"chain": [
            {"provider": "mock", "base_url": f"http://127.0.0.1:{vision_port}/v1",
             "api_key": "vk", "model": "mock-vlm"},
        ], "mode": "describe", "max_tokens": 256, "timeout": 20},
        "upstream": {"base_url": f"http://127.0.0.1:{upstream_port}/v1",
                     "api_key": "uk", "model": "deepseek-v4-flash", "format": "openai"},
        "generation": {"provider": "custom", "base_url": "", "api_key": "", "model": ""},
    }


def _messages(with_image=True):
    content = [{"type": "text", "text": "帮我看看"}]
    if with_image:
        content.append({"type": "image_url", "image_url": {"url": IMG}})
    return [{"role": "user", "content": content}]


def test_build_openai_payload_replaces_images(mock_vision):
    cfg = _cfg(0, mock_vision["port"])
    payload = {"model": "m", "messages": _messages()}
    out = chat.build_openai_payload(cfg, payload)
    content = out["messages"][0]["content"]
    assert all(c["type"] != "image_url" for c in content)
    joined = "".join(c.get("text", "") for c in content)
    assert "[Image 1]" in joined
    assert "视觉描述" in joined
    # 原 payload 不被修改
    assert any(c.get("type") == "image_url" for c in payload["messages"][0]["content"])


def test_build_openai_payload_passthrough_without_image(mock_vision):
    cfg = _cfg(0, mock_vision["port"])
    payload = {"model": "m", "messages": [{"role": "user", "content": "纯文本"}]}
    out = chat.build_openai_payload(cfg, payload)
    assert out["messages"][0]["content"] == "纯文本"


def test_chat_openai_nonstream(mock_vision, mock_upstream):
    cfg = _cfg(mock_upstream["port"], mock_vision["port"])
    text = chat.chat_openai(cfg, {"model": "m", "messages": _messages()})
    assert text == "上游回复"
    # 上游收到的是纯文本请求
    sent = json.loads(mock_upstream["rec"].requests[-1])
    content = sent["messages"][0]["content"]
    assert all(c["type"] != "image_url" for c in content)


def test_chat_openai_stream(mock_vision, mock_upstream):
    cfg = _cfg(mock_upstream["port"], mock_vision["port"])
    deltas = list(chat.iter_chat(cfg, {"model": "m", "messages": _messages()}))
    assert "".join(deltas) == "你好"
    sent = json.loads(mock_upstream["rec"].requests[-1])
    assert sent.get("stream") is True


def test_chat_openai_upstream_error(mock_vision):
    cfg = _cfg(1, mock_vision["port"])  # 端口 1 无服务 → HttpError
    cfg["upstream"]["base_url"] = "http://127.0.0.1:1/v1"
    with pytest.raises(chat.ChatError):
        chat.chat_openai(cfg, {"model": "m", "messages": _messages()})
    with pytest.raises(chat.ChatError):
        list(chat.iter_chat(cfg, {"model": "m", "messages": _messages()}))


def test_extract_openai_text():
    assert chat.extract_openai_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
    assert chat.extract_openai_text({"choices": []}) == ""
