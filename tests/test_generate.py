"""文生图客户端测试(离线,mock HTTP)。"""

import base64

import pytest

import deepsee.generate as gen_mod
from deepsee.generate import GenError, ImageGenClient
from deepsee.util import HttpError


def test_generate_base64(monkeypatch, tmp_path):
    png = b"\x89PNG\r\n\x1a\nbase64-img"
    calls = {}

    def fake(url, payload, headers, timeout=None, method=None):
        calls["payload"] = payload
        return 200, {"data": [{"b64_json": base64.b64encode(png).decode()}]}

    monkeypatch.setattr(gen_mod, "http_json", fake)
    c = ImageGenClient("https://g/v1", "k", "flux-m", provider="siliconflow")
    out = c.generate("一只柴犬", save_dir=str(tmp_path))
    assert out.read_bytes() == png
    assert calls["payload"]["model"] == "flux-m"
    assert calls["payload"]["prompt"] == "一只柴犬"


def test_generate_url(monkeypatch, tmp_path):
    png = b"PNG-from-url"

    def fake_json(url, payload, headers, timeout=None, method=None):
        return 200, {"data": [{"url": "https://img/x.png"}]}

    def fake_download(url, timeout=30):
        assert url == "https://img/x.png"
        return png

    monkeypatch.setattr(gen_mod, "http_json", fake_json)
    monkeypatch.setattr("deepsee.util.download_image_bytes", fake_download)
    c = ImageGenClient("https://g/v1", "k", "m")
    out = c.generate("猫", save_dir=str(tmp_path))
    assert out.read_bytes() == png


def test_generate_error(monkeypatch):
    def fake(url, payload, headers, timeout=None, method=None):
        raise HttpError(429, "rate limited")

    monkeypatch.setattr(gen_mod, "http_json", fake)
    c = ImageGenClient("https://g/v1", "k", "m")
    with pytest.raises(GenError):
        c.generate("猫")


def test_generate_requires_model():
    c = ImageGenClient("https://g/v1", "k", "")
    with pytest.raises(GenError):
        c.generate("猫")
