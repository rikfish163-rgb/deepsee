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

    # 视觉提示应带上用户问题,避免先泛化描述再让 DeepSeek 猜重点。
    vision_request = json.loads(mock_vision["rec"].requests[-1])
    vision_prompt = vision_request["messages"][0]["content"][-1]["text"]
    assert "帮我看看" in vision_prompt


def test_openai_string_image_url_and_responses_input_image(mock_vision):
    cfg = _cfg(0, mock_vision["port"])
    payload = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "input_text", "text": "读取截图错误"},
        {"type": "image_url", "image_url": IMG},
        {"type": "input_image", "image_url": IMG},
    ]}]}
    out = chat.build_openai_payload(cfg, payload)
    content = out["messages"][0]["content"]
    assert all(c["type"] not in ("image_url", "input_image") for c in content)
    joined = "".join(c.get("text", "") for c in content)
    assert "[Image 1" in joined and "[Image 2" in joined
    assert "读取截图错误" in json.loads(mock_vision["rec"].requests[-1])["messages"][0]["content"][-1]["text"]


def test_anthropic_url_image_uses_question_context():
    class FakeSeer:
        def __init__(self):
            self.calls = []

        def describe(self, source, mode="", question="", verbose=False):
            self.calls.append((source, mode, question))
            return "错误截图描述", "mock"

    seer = FakeSeer()
    conv = chat.Converter(seer)
    out, count = conv.anthropic_content([
        {"type": "text", "text": "这个报错怎么修？"},
        {"type": "image", "source": {"type": "url", "url": "https://example.com/error.png"}},
    ])
    assert count == 1
    assert seer.calls[0][0] == "https://example.com/error.png"
    assert seer.calls[0][1] == "qa"
    assert "这个报错怎么修" in seer.calls[0][2]
    assert "视觉内容" in "".join(x.get("text", "") for x in out)


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
