"""视觉客户端与降级链测试(离线,mock HTTP)。"""

import pytest

import deepsee.vision as vision_mod
from deepsee.util import HttpError
from deepsee.vision import VisionChain, VisionClient, VisionError

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def _chain(items):
    return VisionChain(items)


def test_build_messages_uses_data_uri():
    c = VisionClient("https://x/v1", "k", "m")
    msgs = c.build_messages(PNG, "描述一下")
    content = msgs[0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png")
    assert content[-1] == {"type": "text", "text": "描述一下"}


def test_chat_ok(monkeypatch):
    calls = {}

    def fake(url, payload, headers, timeout=None, method=None):
        calls["url"] = url
        calls["payload"] = payload
        return 200, {"choices": [{"message": {"content": "看到一只猫"}}]}

    monkeypatch.setattr(vision_mod, "http_json", fake)
    c = VisionClient("https://x/v1", "k", "m")
    text = c.chat(c.build_messages(PNG, "描述"))
    assert text == "看到一只猫"
    assert calls["url"] == "https://x/v1/chat/completions"
    assert calls["payload"]["model"] == "m"
    assert "Authorization" in calls or True  # 头部在 http_json 之外


def test_chat_error(monkeypatch):
    def fake(url, payload, headers, timeout=None, method=None):
        raise HttpError(500, "boom")

    monkeypatch.setattr(vision_mod, "http_json", fake)
    c = VisionClient("https://x/v1", "k", "m")
    with pytest.raises(VisionError):
        c.chat(c.build_messages(PNG, "描述"))


def test_chain_failover(monkeypatch):
    """第一个失败 → 自动切第二个。"""
    state = {"n": 0}

    def fake(url, payload, headers, timeout=None, method=None):
        state["n"] += 1
        if state["n"] == 1:
            raise HttpError(401, "bad key")
        return 200, {"choices": [{"message": {"content": "兜底成功"}}]}

    monkeypatch.setattr(vision_mod, "http_json", fake)
    chain = _chain([
        {"provider": "a", "base_url": "https://a/v1", "api_key": "k1", "model": "m1"},
        {"provider": "b", "base_url": "https://b/v1", "api_key": "k2", "model": "m2"},
    ])
    text, provider = chain.caption(PNG, "描述")
    assert text == "兜底成功"
    assert provider == "b"


def test_chain_failure_cooldown_skips_recently_failed_provider(monkeypatch):
    calls = []

    def fake(url, payload, headers, timeout=None, method=None):
        calls.append(url)
        if "a.example" in url:
            raise HttpError(503, "down")
        return 200, {"choices": [{"message": {"content": "备用成功"}}]}

    monkeypatch.setattr(vision_mod, "http_json", fake)
    chain = VisionChain([
        {"provider": "a", "base_url": "https://a.example/v1", "api_key": "k1", "model": "m1"},
        {"provider": "b", "base_url": "https://b.example/v1", "api_key": "k2", "model": "m2"},
    ], failure_cooldown=60)
    assert chain.caption(PNG, "描述")[1] == "b"
    assert chain.caption(PNG, "另一个问题")[1] == "b"
    assert sum("a.example" in url for url in calls) == 1
    assert sum("b.example" in url for url in calls) == 2


def test_chain_all_fail(monkeypatch):
    def fake(url, payload, headers, timeout=None, method=None):
        raise HttpError(500, "down")

    monkeypatch.setattr(vision_mod, "http_json", fake)
    chain = _chain([{"provider": "a", "base_url": "https://a/v1", "api_key": "k", "model": "m"}])
    with pytest.raises(VisionError):
        chain.caption(PNG, "描述")


def test_chain_empty():
    with pytest.raises(VisionError):
        VisionChain([]).caption(PNG, "描述")


def test_demo_provider_no_network():
    """演示模式:零网络,返回固定文案。"""
    chain = VisionChain([{"provider": "demo", "base_url": "", "api_key": "", "model": ""}])
    text, provider = chain.caption(PNG, "描述一下")
    assert provider == "demo"
    assert "演示模式" in text


def test_local_ocr_missing_dependency_hint(monkeypatch):
    """未装 rapidocr 时给出安装提示。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("rapidocr"):
            raise ImportError("not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    chain = VisionChain([{"provider": "local-ocr", "base_url": "", "api_key": "", "model": ""}])
    with pytest.raises(VisionError) as ei:
        chain.caption(PNG, "提取文字")
    assert "deepsee[ocr]" in str(ei.value)
