"""识图逻辑测试。"""

import base64
from pathlib import Path

import pytest

from deepsee import caption
from deepsee.util import HttpError

PNG = b"\x89PNG\r\n\x1a\n" + b"xxxx"


def test_build_prompt_modes():
    assert "详尽描述" in caption.build_prompt("describe")
    assert "逐字提取" in caption.build_prompt("ocr")
    assert caption.build_prompt("qa", "图中是什么狗?") == "图中是什么狗?"
    assert "详尽描述" in caption.build_prompt("unknown")  # 兜底


def test_image_ref_from_path(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(PNG)
    ref = caption.image_ref_from_source(str(p))
    assert ref.startswith("data:image/png;base64,")


def test_image_ref_from_url():
    ref = caption.image_ref_from_source("https://a.com/b.png")
    assert ref == "https://a.com/b.png"


def test_image_ref_from_data_uri():
    uri = "data:image/png;base64,AAAA"
    assert caption.image_ref_from_source(uri) == uri


def test_image_ref_rejects_non_image(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hi")
    with pytest.raises(Exception):
        caption.image_ref_from_source(str(p))


def test_seer_describe(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "deepsee.caption.VisionChain.caption",
        lambda self, ref, prompt, image_ref2=None, verbose=False: ("看见一只猫", "mock"),
    )
    p = tmp_path / "c.png"
    p.write_bytes(PNG)
    s = caption.Seer([{"provider": "mock", "base_url": "x", "api_key": "k", "model": "m"}])
    text, provider = s.describe(str(p))
    assert text == "看见一只猫"
    assert provider == "mock"


def test_seer_describe_bytes(monkeypatch):
    monkeypatch.setattr(
        "deepsee.caption.VisionChain.caption",
        lambda self, ref, prompt, image_ref2=None, verbose=False: ("字节图 ok", "mock"),
    )
    s = caption.Seer([{"provider": "mock", "base_url": "x", "api_key": "k", "model": "m"}])
    text, _ = s.describe_bytes(PNG)
    assert text == "字节图 ok"


def test_sniff_mime():
    assert caption._sniff_mime(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert caption._sniff_mime(b"\xff\xd8\xffrest") == "image/jpeg"
    assert caption._sniff_mime(b"RIFFxxxxWEBP") == "image/webp"
    assert caption._sniff_mime(b"unknown") == "image/png"
