"""识图:三种模式(describe/ocr/qa)的 prompt 与统一入口。"""

from __future__ import annotations

import base64

from .config import vision_chain
from .util import (
    _resize,
    capture_screenshot,
    is_image_path,
    latest_session_image,
    read_image_b64,
)
from .vision import VisionChain, VisionError

MODES = ("describe", "ocr", "qa")

PROMPTS = {
    "describe": (
        "请用中文详尽描述这张图片的全部内容:场景、主体、物体、文字、颜色、"
        "布局、坐标关系(如有)、可能的截图上下文。不要遗漏任何可见信息,"
        "以便一个只能读文字的大模型能完整「看到」这张图。"
    ),
    "ocr": (
        "请逐字提取这张图片中的所有文字内容,保留原有排版顺序。"
        "如果图里有代码/表格,请原样输出。没有文字则回复:无文字。"
    ),
}


def build_prompt(mode: str, question: str = "") -> str:
    if mode == "qa":
        return question or "请用中文回答关于这张图片的问题。"
    return PROMPTS.get(mode, PROMPTS["describe"])


def bytes_to_data_uri(data: bytes, max_px: int = 0) -> str:
    """内存字节 → data URI;超尺寸可压缩(压缩后为 JPEG)。"""
    if max_px:
        resized = _resize(data, max_px)
        if resized is not None:
            return "data:image/jpeg;base64," + base64.b64encode(resized).decode()
    return "data:" + _sniff_mime(data) + ";base64," + base64.b64encode(data).decode()


def image_ref_from_source(source: str, max_px: int = 0) -> str:
    """把 路径 / URL / 剪贴板(留空) 归一化为 data URI 或 URL。"""
    if not source:
        p = latest_session_image() or capture_screenshot()
        if not p:
            raise VisionError("无法获得图片:请提供路径/URL,或先截图/复制图片再调用")
        return read_image_b64(p, max_px=max_px)
    if source.startswith(("http://", "https://")):
        return source  # 直接传 URL 给视觉 API
    if source.startswith("data:"):
        return source
    if not is_image_path(source):
        raise VisionError(f"不是支持的图片格式: {source}")
    return read_image_b64(source, max_px=max_px)


class Seer:
    """识别入口,绑定配置的降级链。"""

    def __init__(self, chain: list[dict], mode: str = "describe",
                 max_tokens: int = 2048, timeout: int = 60, max_px: int = 1024):
        self.chain = VisionChain(chain, timeout=timeout, max_tokens=max_tokens)
        self.mode = mode
        self.max_px = max_px

    @classmethod
    def from_config(cls, cfg: dict) -> "Seer":
        v = cfg.get("vision", {})
        return cls(
            vision_chain(cfg),
            mode=v.get("mode", "describe"),
            max_tokens=v.get("max_tokens", 2048),
            timeout=v.get("timeout", 60),
            max_px=v.get("max_px", 1024),
        )

    def _mode(self, mode: str) -> str:
        mode = mode or self.mode
        return mode if mode in MODES else "describe"

    def describe(
        self,
        source: str,
        mode: str = "",
        question: str = "",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """识别 source(路径/URL/剪贴板空串),返回 (文字, provider)。"""
        ref = image_ref_from_source(source, max_px=self.max_px)
        prompt = build_prompt(self._mode(mode), question)
        return self.chain.caption(ref, prompt, verbose=verbose)

    def describe_bytes(
        self,
        data: bytes,
        mode: str = "",
        question: str = "",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """识别内存字节(如网络下载/上传的图片)。"""
        ref = bytes_to_data_uri(data, max_px=self.max_px)
        prompt = build_prompt(self._mode(mode), question)
        return self.chain.caption(ref, prompt, verbose=verbose)


def _sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
        return "image/tiff"
    return "image/png"
