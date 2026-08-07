"""与上游纯文本模型对话:图片块→文字描述→转发 DeepSeek。

代理(proxy.py)与 Web 面板(web.py)共用同一入口,保证各处行为一致。
"""

from __future__ import annotations

import json
from typing import Iterator

from .caption import Seer
from .util import HttpError, http_json, http_stream, safe_join
from .vision import VisionError


class ChatError(Exception):
    def __init__(self, status: int, msg: str):
        self.status = status
        self.msg = msg
        super().__init__(msg)


# ---------------------------------------------------------------------------
# 图片 → 文字 替换(OpenAI / Anthropic 两种内容结构)
# ---------------------------------------------------------------------------


class Converter:
    """把 OpenAI / Anthropic 请求里的图片块替换为文字描述。"""

    def __init__(self, seer: Seer):
        self.seer = seer

    def _describe(self, ref: str, idx: int) -> str:
        try:
            text, _ = self.seer.describe(ref)
        except VisionError as e:
            # 视觉挂了也要让对话继续:给个占位描述
            return f"[Image {idx} 无法识别: {str(e)[:80]}]"
        return f"[Image {idx}] {text}"

    def openai_messages(self, messages: list) -> tuple[list, int]:
        """返回 (新 messages, 转换图片数)。"""
        out: list = []
        n = 0
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                new_content: list[dict] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        n += 1
                        ref = item.get("image_url", {}).get("url", "")
                        new_content.append({"type": "text", "text": self._describe(ref, n)})
                    else:
                        new_content.append(item)
                m = {**m, "content": new_content}
            out.append(m)
        return out, n

    def anthropic_content(self, content: list) -> tuple[list, int]:
        """返回 (新 content 块, 转换图片数)。"""
        out: list[dict] = []
        n = 0
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "image":
                n += 1
                src = blk.get("source", {})
                media = src.get("media_type", "image/png")
                data = src.get("data", "")
                if not data:
                    out.append({"type": "text", "text": f"[Image {n} 空] "})
                    continue
                ref = f"data:{media};base64,{data}"
                out.append({"type": "text", "text": self._describe(ref, n)})
            else:
                out.append(blk)
        return out, n


# ---------------------------------------------------------------------------
# 上游调用(OpenAI 协议)
# ---------------------------------------------------------------------------


def build_openai_payload(cfg: dict, payload: dict) -> dict:
    """把 payload 里的 image_url 块替换为文字描述,返回新 payload(不改原对象)。"""
    conv = Converter(Seer.from_config(cfg))
    messages = payload.get("messages", [])
    new_messages, _ = conv.openai_messages(messages)
    out = dict(payload)
    out["messages"] = new_messages
    return out


def _upstream(cfg: dict) -> tuple[str, str, dict]:
    up = cfg.get("upstream", {})
    base = up.get("base_url", "https://api.deepseek.com/v1")
    api_key = up.get("api_key", "")
    return base, api_key, {"Authorization": f"Bearer {api_key}"}


def extract_openai_text(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def chat_openai(cfg: dict, payload: dict, timeout: int = 120) -> str:
    """非流式对话,返回完整回复文本。"""
    payload = build_openai_payload(cfg, payload)
    base, _key, headers = _upstream(cfg)
    url = safe_join(base, "chat/completions")
    try:
        status, data = http_json(url, payload, headers, timeout=timeout)
    except HttpError as e:
        raise ChatError(502 if e.status else 503, f"upstream {e}") from e
    if status >= 400:
        raise ChatError(status, f"upstream HTTP {status}: {str(data)[:300]}")
    return extract_openai_text(data)


def iter_chat(cfg: dict, payload: dict, timeout: int = 120) -> Iterator[str]:
    """流式对话,产出内容增量文本。图片已自动转文字。"""
    payload = build_openai_payload(cfg, payload)
    payload["stream"] = True
    base, _key, headers = _upstream(cfg)
    url = safe_join(base, "chat/completions")
    try:
        lines = http_stream(url, payload, headers, timeout=timeout)
    except HttpError as e:
        raise ChatError(502 if e.status else 503, f"upstream {e}") from e
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {}).get("content")
        if delta:
            yield delta
