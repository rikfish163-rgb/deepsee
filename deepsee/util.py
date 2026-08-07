"""通用工具:图片判定、base64、缩放、剪贴板截图、会话捞图、HTTP/SSE、ANSI。"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径 & 图片
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".heic", ".heif", ".ico", ".tiff", ".svg",
}

# 1x1 透明 PNG(视觉自检用)
TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

CONFIG_DIR_ENV = "DEEPSEE_HOME"


def is_image_path(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def config_home() -> Path:
    env = os.environ.get(CONFIG_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".deepsee"


def ensure_config_dir() -> Path:
    d = config_home()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def tmp_image_path(suffix: str = ".png") -> Path:
    """唯一临时图片路径(并发安全)。"""
    fd, p = tempfile.mkstemp(suffix=suffix, prefix="deepsee_")
    os.close(fd)
    return Path(p)


def read_json_file(path: Path, default: dict) -> dict:
    """容错读 JSON:不存在或解析失败都返回 default。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def mask_secrets(obj) -> None:
    """原地把 dict 里的 api_key 掩码(保留后 4 位)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "api_key" and isinstance(v, str) and v:
                obj[k] = "·" * max(3, len(v) - 4) + v[-4:]
            else:
                mask_secrets(v)
    elif isinstance(obj, list):
        for v in obj:
            mask_secrets(v)


def read_image_b64(path: str | os.PathLike, max_px: int = 0) -> str:
    """读图片为 data URI。若 Pillow 可用且 max_px>0,超尺寸先压缩(省 token/费用)。

    压缩会把图片重编码为 JPEG,此时 data URI 的 mime 一并改为 image/jpeg。
    """
    data = Path(path).read_bytes()
    resized = False
    if max_px and data:
        r = _resize(data, max_px)
        if r is not None:
            data, resized = r, True
    if resized:
        mime = "image/jpeg"
    else:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _resize(data: bytes, max_px: int) -> bytes | None:
    try:
        from PIL import Image
        import io
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) <= max_px:
            return None
        scale = max_px / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


def download_image_bytes(url: str, timeout: int = 30) -> bytes:
    """下载网络图片为字节(供生成/识别时喂给视觉 API)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "deepsee/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# HTTP(零依赖 urllib)与 SSE
# ---------------------------------------------------------------------------


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:300]}")


def http_json(
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
    method: str | None = None,
):
    """POST/GET JSON,返回 (status, json)。失败抛 HttpError。"""
    data = None
    hdrs = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")) from e
    except urllib.error.URLError as e:
        raise HttpError(0, str(e)) from e
    except ssl.SSLError as e:
        raise HttpError(0, f"SSL: {e}") from e
    except TimeoutError:
        raise HttpError(0, "timeout") from None
    try:
        return status, json.loads(body.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


def http_stream(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: int = 120,
):
    """流式请求,返回逐行迭代器(SSE 行)。失败抛 HttpError。"""
    hdrs = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")) from e
    except urllib.error.URLError as e:
        raise HttpError(0, str(e)) from e

    def gen():
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                yield line.decode("utf-8", "replace")
        finally:
            resp.close()

    return gen()


def safe_join(base: str, *parts: str) -> str:
    """拼 URL 且避免双斜杠。"""
    url = base.rstrip("/")
    for p in parts:
        p = p.strip("/")
        if p:
            url += "/" + p
    return url


# ---------------------------------------------------------------------------
# 剪贴板截图(跨平台)
# ---------------------------------------------------------------------------


def capture_screenshot() -> Path | None:
    """截取当前屏幕/剪贴板图片,存唯一临时文件返回路径;失败返回 None。"""
    png = tmp_image_path()
    if sys.platform.startswith("linux"):
        return _capture_linux(png)
    if sys.platform == "darwin":
        return _capture_macos(png)
    return _capture_windows(png)


def _run(cmd: list[str], timeout: int = 10) -> bool:
    try:
        subprocess.run(cmd, check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _capture_linux(png: Path) -> Path | None:
    # 1) 剪贴板里已有图片
    for cmd in (["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                ["wl-paste", "-t", "image/png"]):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5)
            if out.returncode == 0 and out.stdout:
                png.write_bytes(out.stdout)
                return png
        except Exception:
            pass
    # 2) 全屏/选区截图工具
    if shutil.which("gnome-screenshot"):
        if _run(["gnome-screenshot", "-a", "-f", str(png)]):
            return png
    if shutil.which("spectacle"):
        if _run(["spectacle", "-b", "-n", "-o", str(png)]):
            return png
    if shutil.which("scrot"):
        if _run(["scrot", "-s", str(png)]):
            return png
    if shutil.which("import"):  # ImageMagick
        if _run(["import", str(png)]):
            return png
    if shutil.which("flameshot"):
        if _run(["flameshot", "gui", "-s", str(png)]):
            return png
    return None


def _capture_macos(png: Path) -> Path | None:
    if shutil.which("pngpaste") and _run(["pngpaste", str(png)]):
        return png
    if _run(["screencapture", "-x", "-i", str(png)]):
        return png
    return None


def _capture_windows(png: Path) -> Path | None:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$b=[System.Windows.Forms.Clipboard]::GetImage();"
        f"if($b){{$b.Save('{png}')}}"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=10)
        if r.returncode == 0 and png.exists():
            return png
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 从 Agent 会话日志捞最近图片(see --latest)
# ---------------------------------------------------------------------------

# log路径 -> (st_mtime_ns, 该文件最后一张图bytes 或 None);增量跳过未变文件
_file_cache: dict[str, tuple[int, bytes | None]] = {}


def latest_session_image(agent: str = "auto") -> Path | None:
    """在 Claude Code / Codex 会话日志中找最近的图片并落盘,返回路径。"""
    home = Path.home()
    roots: list[tuple[Path, str]] = []
    cc = home / ".claude" / "projects"
    if cc.is_dir():
        roots.append((cc, "claude"))
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        p = Path(os.environ["CLAUDE_PROJECT_DIR"]) / ".claude" / "projects"
        if p.is_dir():
            roots.append((p, "claude"))
    codex = home / ".codex" / "sessions"
    if codex.is_dir():
        roots.append((codex, "codex"))

    newest: tuple[int, bytes] | None = None
    for root, tag in roots:
        for log in root.glob("**/*.jsonl"):
            data = _last_image_from_log(log, tag)
            if data is None:
                continue
            try:
                mt = log.stat().st_mtime_ns
            except OSError:
                mt = 0
            if newest is None or mt >= newest[0]:
                newest = (mt, data)
    if newest is None:
        return None
    out = tmp_image_path()
    out.write_bytes(newest[1])
    return out


def _last_image_from_log(path: Path, agent: str) -> bytes | None:
    """返回该日志文件里最后一张图(文件未变则用缓存,增量解析)。"""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    key = str(path)
    cached = _file_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    last: bytes | None = None
    try:
        with path.open("rb") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", "replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                data = _extract_image_from_obj(obj, agent)
                if data:
                    last = data
    except OSError:
        return None
    _file_cache[key] = (mtime, last)
    return last


def _extract_image_from_obj(obj: dict, agent: str) -> bytes | None:
    if not isinstance(obj, dict):
        return None
    if agent == "claude":
        # PreToolUse / UserMessage 里的图片附件
        v = obj.get("message")
        if isinstance(v, dict):
            content = v.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "image":
                        src = c.get("source")
                        if isinstance(src, dict) and src.get("data"):
                            return _b64_bytes(src["data"])
                    if c.get("type") == "tool_use" and c.get("name") == "Read":
                        fp = c.get("input", {}).get("file_path")
                        if fp and is_image_path(fp):
                            try:
                                return Path(fp).read_bytes()
                            except OSError:
                                pass
        # 顶层 image_path / file_path
        fp = obj.get("image_path") or obj.get("file_path")
        if isinstance(fp, str) and is_image_path(fp):
            try:
                return Path(fp).read_bytes()
            except OSError:
                pass
    else:  # codex: session 里存了图片的 base64 + 文件路径
        items = obj.get("input") or obj.get("content") or obj.get("output") or obj
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("type") in ("input_image", "image"):
                    for key in ("data", "image_url", "file_data"):
                        val = it.get(key)
                        if isinstance(val, str) and val:
                            return _b64_bytes(val)
        for key in ("data", "image_url", "file_data", "base64"):
            val = obj.get(key)
            if isinstance(val, str) and val and len(val) > 100:
                return _b64_bytes(val)
    return None


def _b64_bytes(s: str) -> bytes:
    s = s.strip()
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s)
    except Exception:
        return b""


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def cprint(text: str, color: str = "", bold: bool = False) -> None:
    """带 ANSI 颜色的打印;非 tty 自动降级为纯文本。"""
    colors = {
        "red": "31", "green": "32", "yellow": "33",
        "blue": "34", "magenta": "35", "cyan": "36",
    }
    if not sys.stdout.isatty():
        print(text)
        return
    code = []
    if color in colors:
        code.append(colors[color])
    if bold:
        code.append("1")
    if not code:
        print(text)
        return
    print(f"\033[{';'.join(code)}m{text}\033[0m")


def truncate(text: str, n: int = 400) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."
