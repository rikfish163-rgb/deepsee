"""Claude Code PreToolUse Hook:拦截 Read 图片,换成文字描述。

- 模型命中 target_models(默认 deepseek)或未配置目标列表 → 图片转文字
- 任何异常都原样透传,**绝不阻断 Read**
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .caption import Seer
from .config import load, vision_chain
from .util import is_image_path


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
    for env in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "CLAUDE_CODE_MODEL"):
        v = os.environ.get(env)
        if v:
            return v
    return ""


def _should_bridge(model: str, targets: list) -> bool:
    """是否需要对当前模型做图片桥接。空目标列表 = 全部桥接;模型未知 = 桥接。"""
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
    agents = cfg.get("agents", {}).get("claude_code", {})
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input") or {}

    # 只关心 Read 图片
    if tool_name != "Read":
        return 0
    fp = tool_input.get("file_path")
    if not isinstance(fp, str) or not is_image_path(fp):
        return 0

    # 模型过滤:命中非目标模型(且配了目标列表)→ 透传
    model = _detect_model(hook_input)
    if not _should_bridge(model, agents.get("target_models") or []):
        return 0

    # 缺视觉配置 → 透传(不打扰会话)
    if not vision_chain(cfg):
        return 0
    try:
        seer = Seer.from_config(cfg)
        text, _provider = seer.describe(fp)
    except Exception:  # noqa: BLE001 失败必须透传
        return 0

    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="deepsee_hook_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "hookSpecificOutput": {"updatedInput": {"file_path": tmp}},
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return run_stdin()
    except Exception:  # noqa: BLE001
        return 0
