"""识图:三种模式(describe/ocr/qa)的 prompt 与统一入口。"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from collections import OrderedDict

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

TASK_PROMPTS = {
    "error": (
        "这是一张报错或故障截图。请准确抄录报错文本、错误码、堆栈和相关界面状态，"
        "区分可见事实与推测，并指出最可能影响排障的细节。"
    ),
    "diagram": (
        "这是一张技术图或架构图。请按节点、连线方向、分组、标签和数据流完整转写，"
        "保留技术名词与层级关系。"
    ),
    "chart": (
        "这是一张图表。请识别标题、坐标轴、图例、单位、关键数值、趋势与异常点；"
        "看不清的数值要明确标注,不要猜测。"
    ),
    "ui": (
        "这是一张用户界面。请描述页面结构、组件、文字、状态、位置关系和可操作元素，"
        "并特别记录禁用、选中、弹窗或错误状态。"
    ),
    "screenshot": (
        "请把截图中的窗口、应用、可见文字、控件状态和上下文完整转写，"
        "优先记录与用户问题直接相关的区域。"
    ),
    "compare": (
        "请按图片 1、图片 2……的顺序比较这些图片，列出共同点、差异、变化位置和"
        "可能影响结论的细节；只依据可见内容，不要虚构。"
    ),
}


def infer_task(question: str) -> str:
    """从问题中的高置信关键词选择专用视觉提示;不确定时保持通用模式。"""
    q = (question or "").lower()
    if any(k in q for k in ("报错", "错误", "异常", "error", "traceback", "崩溃")):
        return "error"
    if any(k in q for k in ("架构图", "流程图", "拓扑", "diagram", "电路图", "技术图")):
        return "diagram"
    if any(k in q for k in ("图表", "曲线", "柱状", "折线", "chart", "趋势")):
        return "chart"
    if any(k in q for k in ("界面", "按钮", "页面", "ui", "弹窗")):
        return "ui"
    if any(k in q for k in ("截图", "screen")):
        return "screenshot"
    return ""


def build_prompt(mode: str, question: str = "", task: str = "") -> str:
    task = task or infer_task(question)
    if mode == "qa" and not task:
        return question or "请用中文回答关于这张图片的问题。"
    if mode == "ocr":
        return PROMPTS["ocr"] + (f"\n用户特别关注：{question}" if question else "")
    base = TASK_PROMPTS.get(task, PROMPTS.get(mode, PROMPTS["describe"]))
    if question:
        return f"{base}\n\n用户问题：{question}\n请优先提供回答该问题所需的视觉事实。"
    return base


def bytes_to_data_uri(data: bytes, max_px: int = 0) -> str:
    """内存字节 → data URI;超尺寸可压缩(压缩后为 JPEG)。"""
    if max_px:
        resized = _resize(data, max_px)
        if resized is not None:
            return "data:image/jpeg;base64," + base64.b64encode(resized).decode()
    return "data:" + _sniff_mime(data) + ";base64," + base64.b64encode(data).decode()


def _resize_data_uri(ref: str, max_px: int) -> str:
    """大图(data URI)压缩到 max_px 再重编码 JPEG;无 Pillow/小图则透传。"""
    if max_px <= 0 or not ref.startswith("data:image/"):
        return ref
    try:
        raw = base64.b64decode(ref.split(",", 1)[1])
        if len(raw) < 512 * 1024:  # <512KB 直接透传,省解码 CPU
            return ref
        resized = _resize(raw, max_px)
        if resized is None:
            return ref
        return "data:image/jpeg;base64," + base64.b64encode(resized).decode()
    except Exception:  # noqa: BLE001
        return ref


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
        return _resize_data_uri(source, max_px)
    if not is_image_path(source):
        raise VisionError(f"不是支持的图片格式: {source}")
    return read_image_b64(source, max_px=max_px)


class Seer:
    """识别入口,绑定配置的降级链。"""

    def __init__(
        self,
        chain: list[dict],
        mode: str = "describe",
        max_tokens: int = 2048,
        timeout: int = 60,
        max_px: int = 1024,
        cache_ttl: int = 300,
        cache_max_entries: int = 128,
        failure_cooldown: int = 30,
    ):
        self.chain = VisionChain(
            chain,
            timeout=timeout,
            max_tokens=max_tokens,
            failure_cooldown=failure_cooldown,
        )
        self.mode = mode
        self.max_px = max_px
        self.cache_ttl = max(0, int(cache_ttl or 0))
        self.cache_max_entries = max(0, int(cache_max_entries or 0))
        self._cache: OrderedDict[str, tuple[float, tuple[str, str]]] = OrderedDict()
        self._cache_lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: dict) -> "Seer":
        v = cfg.get("vision", {})
        return cls(
            vision_chain(cfg),
            mode=v.get("mode", "describe"),
            max_tokens=v.get("max_tokens", 2048),
            timeout=v.get("timeout", 60),
            max_px=v.get("max_px", 1024),
            cache_ttl=v.get("cache_ttl", 300),
            cache_max_entries=v.get("cache_max_entries", 128),
            failure_cooldown=v.get("failure_cooldown", 30),
        )

    def _mode(self, mode: str) -> str:
        mode = mode or self.mode
        return mode if mode in MODES else "describe"

    def describe(
        self,
        source: str,
        mode: str = "",
        question: str = "",
        task: str = "",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """识别 source(路径/URL/剪贴板空串),返回 (文字, provider)。"""
        ref = image_ref_from_source(source, max_px=self.max_px)
        prompt = build_prompt(self._mode(mode), question, task=task)
        return self._caption_refs([ref], prompt, verbose=verbose)

    def describe_bytes(
        self,
        data: bytes,
        mode: str = "",
        question: str = "",
        task: str = "",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """识别内存字节(如网络下载/上传的图片)。"""
        ref = bytes_to_data_uri(data, max_px=self.max_px)
        prompt = build_prompt(self._mode(mode), question, task=task)
        return self._caption_refs([ref], prompt, verbose=verbose)

    def compare(
        self,
        sources: list[str],
        question: str = "",
        task: str = "compare",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """比较 2–4 张路径/URL/data URI 图片。"""
        if not isinstance(sources, list) or not 2 <= len(sources) <= 4:
            raise VisionError("图片对比需要 2–4 个图片来源")
        refs = [image_ref_from_source(source, max_px=self.max_px) for source in sources]
        prompt = build_prompt("describe", question, task=task or "compare")
        return self._caption_refs(refs, prompt, verbose=verbose)

    def compare_bytes(
        self,
        images: list[bytes],
        question: str = "",
        task: str = "compare",
        verbose: bool = False,
    ) -> tuple[str, str]:
        """比较 2–4 张内存图片。"""
        if not isinstance(images, list) or not 2 <= len(images) <= 4:
            raise VisionError("图片对比需要 2–4 张图片")
        refs = [bytes_to_data_uri(data, max_px=self.max_px) for data in images]
        prompt = build_prompt("describe", question, task=task or "compare")
        return self._caption_refs(refs, prompt, verbose=verbose)

    def _caption_refs(
        self, refs: list[str], prompt: str, verbose: bool = False
    ) -> tuple[str, str]:
        key = self._cache_key(refs, prompt)
        if key:
            now = time.monotonic()
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached and now - cached[0] <= self.cache_ttl:
                    self._cache.move_to_end(key)
                    return cached[1]
                if cached:
                    self._cache.pop(key, None)

        if len(refs) == 1:
            result = self.chain.caption(refs[0], prompt, verbose=verbose)
        else:
            result = self.chain.caption_many(refs, prompt, verbose=verbose)

        if key:
            with self._cache_lock:
                self._cache[key] = (time.monotonic(), result)
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_max_entries:
                    self._cache.popitem(last=False)
        return result

    def _cache_key(self, refs: list[str], prompt: str) -> str:
        if not self.cache_ttl or not self.cache_max_entries:
            return ""
        digest = hashlib.sha256()
        digest.update(prompt.encode("utf-8", "replace"))
        for ref in refs:
            digest.update(b"\0")
            digest.update(ref.encode("utf-8", "replace"))
        return digest.hexdigest()


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
