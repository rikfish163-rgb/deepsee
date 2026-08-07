"""通用工具:图片判定、base64、缩放、剪贴板截图、会话捞图、HTTP/SSE、ANSI。"""

from __future__ import annotations

import base64
import http.client
import json
import mimetypes
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

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


_SECRET_KEYS = ("api_key", "master_key", "auth_token", "ANTHROPIC_AUTH_TOKEN")


def mask_secrets(obj) -> None:
    """原地把 dict 里的密钥字段(api_key/master_key/auth_token…)掩码(保留后 4 位)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SECRET_KEYS and isinstance(v, str) and v:
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


def download_image_bytes(url: str, timeout: int = 30, max_bytes: int = 20 * 1024 * 1024) -> bytes:
    """下载网络图片为字节(供生成/识别时喂给视觉 API)。

    安全:只允许 http/https,拒绝内网/链路本地/环回地址(SSRF),限制响应大小(防 OOM)。
    """
    import ipaddress
    import socket

    if "://" not in url:
        raise ValueError("URL 缺少 scheme")
    scheme, rest = url.split("://", 1)
    scheme = scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https,收到 {scheme}")
    host = rest.split("/", 1)[0].split(":", 1)[0]
    # 解析并拦截内网地址(SSRF:本地文件/云元数据/内网服务)
    try:
        ip = socket.getaddrinfo(host, None)[0][4][0]
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"拒绝内网/环回地址: {host}")
    except socket.gaierror:
        raise ValueError(f"无法解析域名: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            raise ValueError(f"响应过大(>{max_bytes // 1024 // 1024}MB)")
        data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"响应过大(>{max_bytes // 1024 // 1024}MB)")
        return data


# ---------------------------------------------------------------------------
# HTTP(零依赖 urllib)与 SSE
# ---------------------------------------------------------------------------


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:300]}")


# 部分中继/API 走 Cloudflare,默认 urllib UA 会被 1010 拦,统一用浏览器 UA
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ---- keep-alive 连接池(http.client,避免每请求 TLS 握手) ----

_pool: dict[tuple[str, str, int], http.client.HTTPConnection] = {}
_pool_lock = threading.Lock()
_NET_ERRS = (ConnectionError, socket.timeout, http.client.RemoteDisconnected,
             http.client.CannotSendRequest, http.client.BadStatusLine, OSError)


def _conn_cls(scheme: str):
    return http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection


def _get_pooled(url: str, timeout: int):
    u = urlparse(url)
    scheme = u.scheme or "https"
    host = u.hostname or ""
    port = u.port or (443 if scheme == "https" else 80)
    key = (scheme, host, port)
    with _pool_lock:
        conn = _pool.get(key)
        if conn is None:
            conn = _conn_cls(scheme)(host, port, timeout=timeout)
            _pool[key] = conn
    return conn, key


def _drop_pooled(key, conn) -> None:
    with _pool_lock:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        if _pool.get(key) is conn:
            del _pool[key]


def close_http_pool() -> None:
    """关闭并清空连接池(测试/退出时调用,释放 keep-alive 连接)。"""
    with _pool_lock:
        for c in _pool.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        _pool.clear()


def _url_path(u) -> str:
    p = u.path or "/"
    return p + ("?" + u.query if u.query else "")


def _do_request(conn, method: str, path: str, data, headers):
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    return resp.status, dict(resp.getheaders()), body


def _raw_request(url: str, payload, headers, timeout: int, method: str | None):
    """带连接复用 + 失效重连 + 重定向。返回 (status, rheaders, body)。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    m = (method or ("POST" if data is not None else "GET")).upper()
    cur = url
    for _ in range(6):  # 最多跟随 5 次重定向
        u = urlparse(cur)
        scheme = u.scheme or "https"
        if scheme not in ("http", "https"):
            raise HttpError(0, f"unsupported scheme: {scheme}")
        conn, key = _get_pooled(cur, timeout)
        try:
            status, rheaders, body = _do_request(conn, m, _url_path(u), data, headers)
        except _NET_ERRS as e:
            _drop_pooled(key, conn)  # keep-alive 连接可能已被服务端关闭,换新重试一次
            conn, key = _get_pooled(cur, timeout)
            try:
                status, rheaders, body = _do_request(conn, m, _url_path(u), data, headers)
            except _NET_ERRS as e2:
                _drop_pooled(key, conn)
                raise HttpError(0, f"network: {e2}") from e2
            except ssl.SSLError as e2:
                _drop_pooled(key, conn)
                raise HttpError(0, f"SSL: {e2}") from e2
        except ssl.SSLError as e:
            _drop_pooled(key, conn)
            raise HttpError(0, f"SSL: {e}") from e
        if status in (301, 302, 303, 307, 308):
            loc = rheaders.get("Location")
            if not loc:
                return status, rheaders, body
            if status in (301, 302, 303) and m == "POST":
                m, data = "GET", None
            if loc.startswith(("http://", "https://")):
                cur = loc
            else:
                cur = urlparse(cur)._replace(path="", query="").geturl() + loc
            continue
        return status, rheaders, body
    raise HttpError(0, "too many redirects")


def http_json(
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
    method: str | None = None,
):
    """POST/GET JSON,返回 (status, json)。失败抛 HttpError。"""
    hdrs = {"Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    status, _rh, body = _raw_request(url, payload, hdrs, timeout, method)
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
    hdrs = {"Accept": "text/event-stream", "Content-Type": "application/json",
            "User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    data = json.dumps(payload).encode("utf-8")
    u = urlparse(url)
    scheme = u.scheme or "https"
    if scheme not in ("http", "https"):
        raise HttpError(0, f"unsupported scheme: {scheme}")
    conn = _conn_cls(scheme)(u.hostname or "", u.port or (443 if scheme == "https" else 80),
                             timeout=timeout)
    try:
        conn.request("POST", _url_path(u), body=data, headers=hdrs)
        resp = conn.getresponse()
        if resp.status >= 400:
            body = resp.read().decode("utf-8", "replace")
            conn.close()
            raise HttpError(resp.status, body)
    except _NET_ERRS as e:
        raise HttpError(0, f"network: {e}") from e
    except ssl.SSLError as e:
        raise HttpError(0, f"SSL: {e}") from e

    def gen():
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                yield line.decode("utf-8", "replace")
        finally:
            conn.close()

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
    # 2) Wayland:grim + slurp 选区截图
    if shutil.which("grim") and shutil.which("slurp"):
        try:
            region = subprocess.run(["slurp"], capture_output=True, text=True, timeout=15)
            if region.returncode == 0 and region.stdout.strip():
                if _run(["grim", "-g", region.stdout.strip(), str(png)]):
                    return png
        except Exception:
            pass
    # 3) 全屏/选区截图工具(X11/通用)
    if shutil.which("gnome-screenshot"):
        if _run(["gnome-screenshot", "-a", "-f", str(png)], timeout=30):
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
        if _run(["flameshot", "gui", "-p", str(png)]):  # -p 指定保存路径(不是 -s)
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
        "blue": "34", "magenta": "35", "cyan": "36", "dim": "2",
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
