"""Claude Code PreToolUse Hook:拦截 Read 图片,换成文字描述。

限制(重要,务必读):
- 本 Hook 只拦截「模型主动 Read 图片路径」。
- 粘贴图 / headless(`claude -p`)场景下,Claude Code 对 updatedInput
  可能不生效(平台行为),此时请用 deepsee 代理(协议层转换)或 MCP。
- 任何异常都原样透传,**绝不阻断 Read**。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

from .caption import Seer
from .config import load, vision_chain
from .util import is_image_path

# Claude Code 的 Read 对长文本有截断,描述太长会被截断丢信息
MAX_DESC = 3800
_TMP_TTL = 600  # 临时描述文件保留秒数,之后清理

_HOOK_DIR: Path | None = None


def _hook_dir() -> Path:
    global _HOOK_DIR
    if _HOOK_DIR is None:
        base = Path(os.environ.get("DEEPSEE_HOME", str(Path.home() / ".deepsee")))
        _HOOK_DIR = base / "hooks"
        _HOOK_DIR.mkdir(parents=True, exist_ok=True)
    return _HOOK_DIR


def _cleanup_old() -> None:
    try:
        now = time.time()
        for f in _hook_dir().glob("deepsee_hook_*.txt"):
            if now - f.stat().st_mtime > _TMP_TTL:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def _detect_model(hook_input: dict) -> str:
    """从 hook input / 环境变量猜测当前模型名(尽力而为,可能为空)。"""
    for key in ("model", "model_name"):
        v = hook_input.get(key)
        if isinstance(v, str) and v:
            return v
    model_obj = hook_input.get("model_info") or hook_input.get("model")
    if isinstance(model_obj, dict):
        name = model_obj.get("name") or model_obj.get("id")
        if isinstance(name, str):
            return name
    for env in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "CLAUDE_CODE_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL"):
        v = os.environ.get(env)
        if v:
            return v
    return ""


def _should_bridge(model: str, targets: list) -> bool:
    """是否需要桥接。空目标列表 = 全部桥接;模型未知 = 桥接(装 hook 本意如此)。"""
    if not targets:
        return True
    if not model:
        return True
    m = model.lower()
    return any(t.lower() in m for t in targets)


def run_stdin() -> int:
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # 不是合法输入,直接透传

    cfg = load()
    cc = cfg.get("agents", {}).get("claude_code", {})
    # 配置项 hook:false → 透传(让 `deepsee config set agents.claude_code.hook false` 真正生效)
    if cc.get("hook", True) is False:
        return 0
    _cleanup_old()

    if hook_input.get("tool_name") != "Read":
        return 0
    fp = (hook_input.get("tool_input") or {}).get("file_path")
    if not isinstance(fp, str) or not is_image_path(fp):
        return 0

    # 模型过滤:命中非目标模型(且配了目标列表)→ 透传
    model = _detect_model(hook_input)
    if not _should_bridge(model, cc.get("target_models") or []):
        return 0
    if not vision_chain(cfg):
        return 0

    try:
        seer = Seer.from_config(cfg)
        text, _provider = seer.describe(fp)
    except Exception:  # noqa: BLE001 失败必须透传
        return 0

    txt = _hook_dir() / f"deepsee_hook_{uuid.uuid4().hex[:8]}.txt"
    try:
        txt.write_text((text or "")[:MAX_DESC], encoding="utf-8")
    except OSError:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "hookSpecificOutput": {"updatedInput": {"file_path": str(txt)}},
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def verify() -> int:
    """自检:hook JSON 契约、配置状态与已知限制。"""
    cfg = load()
    cc = cfg.get("agents", {}).get("claude_code", {})
    chain = cfg.get("vision", {}).get("chain", [])
    report = {
        "hook_json_contract": {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "hookSpecificOutput": {"updatedInput": {"file_path": "<temp描述>.txt"}},
            }
        },
        "hook_enabled": cc.get("hook", True),
        "target_models": cc.get("target_models", []),
        "vision_configured": bool(chain),
        "vision_provider": chain[0].get("provider") if chain else None,
        "limits": (
            "updatedInput 仅在交互式会话、模型主动 Read 图片路径时生效;"
            "headless(claude -p)/粘贴图请用 deepsee 代理或 MCP"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    if "--verify" in sys.argv[1:]:
        return verify()
    try:
        return run_stdin()
    except Exception:  # noqa: BLE001
        return 0
