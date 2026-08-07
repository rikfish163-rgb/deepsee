"""文生图:OpenAI 兼容 /images/generations 客户端 + 后端预设。"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from .config import GEN_PROVIDERS
from .util import HttpError, http_json, safe_join


class GenError(Exception):
    pass


class ImageGenClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        size: str = "1024x1024",
        provider: str = "custom",
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.size = size
        self.provider = provider
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg: dict, size: str = "") -> "ImageGenClient":
        gen = cfg.get("generation", {})
        return cls(
            gen.get("base_url", ""),
            gen.get("api_key", ""),
            gen.get("model", ""),
            size=size or gen.get("size", "1024x1024"),
            provider=gen.get("provider", "custom"),
        )

    def generate(self, prompt: str, save_dir: str | None = None) -> Path:
        """生成图片并保存,返回保存路径。"""
        if not self.model:
            raise GenError("未配置文生图模型,请运行 `deepsee setup` 或编辑 config.json")
        url = safe_join(self.base_url, "images/generations")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": self.size,
            "response_format": "url",
        }
        try:
            status, data = http_json(url, payload, headers, timeout=self.timeout)
        except HttpError as e:
            raise GenError(f"[{self.provider}] 文生图失败: {e}") from e
        if status != 200 or data is None:
            raise GenError(f"[{self.provider}] 文生图失败: HTTP {status} {str(data)[:200]}")

        out = Path(save_dir or _default_save_dir()) / _safe_filename(prompt)
        try:
            item = data["data"][0]
        except (KeyError, IndexError, TypeError):
            raise GenError(f"[{self.provider}] 返回格式异常: {str(data)[:200]}")
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            from .util import download_image_bytes

            out.write_bytes(download_image_bytes(item["url"], timeout=self.timeout))
        else:
            raise GenError(f"[{self.provider}] 响应中没有图片数据: {str(data)[:200]}")
        return out


def _default_save_dir() -> Path:
    d = Path(os.environ.get("DEEPSEE_OUTPUT", "~/Pictures/deepsee")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(prompt: str) -> str:
    import re
    import time

    slug = re.sub(r"[^\w一-鿿-]+", "_", prompt)[:40].strip("_")
    if not slug:
        slug = "image"
    return f"{slug}_{int(time.time())}.png"
