"""视觉客户端:OpenAI 兼容 chat/completions 识图,带多供应商降级链。"""

from __future__ import annotations

import threading
from typing import Any

from .util import HttpError, TINY_PNG_DATA_URI, cprint, http_json, safe_join


class VisionError(Exception):
    """视觉识别失败(链路全部失败)。"""


class VisionClient:
    """单个 OpenAI 兼容视觉端点。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        provider: str = "custom",
        timeout: int = 60,
        max_tokens: int = 2048,
        extra_headers: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.extra_headers = extra_headers or {}

    # -- 请求构造 ---------------------------------------------------------
    def build_messages(
        self, image_ref: str, prompt: str, image_ref2: str | None = None
    ) -> list[dict]:
        """image_ref: data URI / http(s) URL。返回 OpenAI content 数组消息。"""
        content: list[dict] = []
        refs = [image_ref] + ([image_ref2] if image_ref2 else [])
        for ref in refs:
            if ref.startswith("data:"):
                content.append({"type": "image_url", "image_url": {"url": ref}})
            else:
                content.append(
                    {"type": "image_url", "image_url": {"url": ref, "detail": "high"}}
                )
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    def chat(self, messages: list[dict]) -> str:
        url = safe_join(self.base_url, "chat/completions")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        headers.update(self.extra_headers)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        try:
            status, data = http_json(url, payload, headers, timeout=self.timeout)
        except HttpError as e:
            raise VisionError(f"[{self.provider}] 视觉 API 异常: {e}") from e
        if status != 200 or data is None:
            raise VisionError(f"[{self.provider}] 视觉 API 异常: {_err_text(status, data)}")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise VisionError(f"[{self.provider}] 视觉 API 返回格式无法解析: {str(data)[:200]}")

    def health(self) -> tuple[bool, str]:
        try:
            self.chat(self.build_messages(TINY_PNG_DATA_URI, "回复:ok"))
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:160]


def _err_text(status: int, data: Any) -> str:
    if data is None:
        return f"HTTP {status}"
    msg = data.get("error", {}).get("message") if isinstance(data, dict) else None
    return msg or f"HTTP {status}: {str(data)[:160]}"


class VisionChain:
    """多供应商降级链:按顺序尝试,失败自动切下一个。"""

    def __init__(self, items: list[dict], timeout: int = 60, max_tokens: int = 2048):
        self.clients: list[VisionClient] = []
        for it in items:
            self.clients.append(
                VisionClient(
                    base_url=it.get("base_url", ""),
                    api_key=it.get("api_key", ""),
                    model=it.get("model", ""),
                    provider=it.get("provider", "custom"),
                    timeout=it.get("timeout") or timeout,
                    max_tokens=it.get("max_tokens") or max_tokens,
                    extra_headers=it.get("headers") or None,
                )
            )

    def caption(
        self,
        image_ref: str,
        prompt: str,
        image_ref2: str | None = None,
        verbose: bool = False,
    ) -> tuple[str, str]:
        """识别一张/两张图,返回 (文字, 成功provider)。全部失败抛 VisionError。

        特殊 provider 无需 API key:
        - demo:      演示模式,返回固定文案,零网络
        - local-ocr: 本地 OCR(rapidocr),离线提取图片文字
        """
        if not self.clients:
            raise VisionError("未配置视觉供应商,请运行 `deepsee setup`")
        last_err = ""
        for c in self.clients:
            if c.provider == "demo":
                text = _demo_text(prompt)
                if verbose:
                    cprint("  ✓ 演示模式(未调用真实 API)", "green")
                return text, "demo"
            if c.provider == "local-ocr":
                try:
                    text = _local_ocr(image_ref, c.provider)
                except VisionError as e:
                    last_err = str(e)
                    if verbose:
                        cprint(f"  ✗ local-ocr 失败: {str(e)[:120]}", "yellow")
                    continue
                if verbose:
                    cprint("  ✓ 本地 OCR 完成", "green")
                return text, "local-ocr"
            try:
                messages = c.build_messages(image_ref, prompt, image_ref2)
                text = c.chat(messages)
                text = (text or "").strip()
                if not text:
                    raise VisionError(f"[{c.provider}] 返回空内容")
                if verbose:
                    from .util import cprint

                    cprint(f"  ✓ 识别成功({c.provider})", "green")
                return text, c.provider
            except (VisionError, HttpError) as e:
                last_err = str(e)
                if verbose:
                    from .util import cprint

                    cprint(f"  ✗ {c.provider} 失败: {str(e)[:120]}", "yellow")
        raise VisionError(f"视觉链路全部失败: {last_err}")


def _demo_text(prompt: str) -> str:
    return (
        "【演示模式 · 未调用任何视觉 API】\n"
        "图片已成功接收,整个流程运转正常。\n"
        "要开始真实识图,任选其一:\n"
        "  1. 智谱 GLM-4V-Flash(免费):跑 `deepsee setup` 重填 api_key\n"
        "  2. 本地 OCR(离线免 key):`pip install 'deepsee[ocr]'`,再把视觉供应商设为 local-ocr\n"
        "  3. 在 Web 面板「配置」页直接编辑 api_key 保存"
    )


def _ref_to_bytes(image_ref: str) -> bytes:
    """把 data URI / URL 解析为图片字节。"""
    if image_ref.startswith("data:"):
        import base64

        return base64.b64decode(image_ref.split(",", 1)[1])
    if image_ref.startswith(("http://", "https://")):
        from .util import download_image_bytes

        return download_image_bytes(image_ref)
    from pathlib import Path

    return Path(image_ref).read_bytes()


_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()


def _local_ocr(image_ref: str, provider: str) -> str:
    """离线 OCR:把图片文字提取为文本(无需任何 API key)。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        with _OCR_LOCK:  # 多线程下只初始化一次
            if _OCR_ENGINE is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as e:
                    raise VisionError(
                        "本地 OCR 未安装:运行 `pip install 'deepsee[ocr]'` 或 `pip install rapidocr-onnxruntime`"
                    ) from e
                try:
                    _OCR_ENGINE = RapidOCR()
                except Exception as e:  # noqa: BLE001
                    raise VisionError(f"本地 OCR 初始化失败: {e}") from e
    try:
        data = _ref_to_bytes(image_ref)
        from .util import tmp_image_path

        tmp = tmp_image_path()  # 唯一临时文件,避免多线程并发覆盖
        try:
            tmp.write_bytes(data)
            result, _elapse = _OCR_ENGINE(tmp)
        finally:
            tmp.unlink(missing_ok=True)
    except VisionError:
        raise
    except Exception as e:  # noqa: BLE001
        raise VisionError(f"本地 OCR 识别失败: {e}") from e
    if not result:
        return "未检测到文字。"
    lines = []
    for item in result:
        # item = [box, text, score]
        try:
            lines.append(item[1])
        except (IndexError, TypeError):
            continue
    return "\n".join(lines)
