"""安装向导:探测终端、写配置、注册 Hook/Skill/MCP/补丁(带备份回滚)。"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import __version__
from .config import GEN_PROVIDERS, PROVIDERS, load, save
from .util import TINY_PNG_DATA_URI, cprint, ensure_config_dir, read_json_file

# ---------------------------------------------------------------------------
# 终端探测
# ---------------------------------------------------------------------------


def detect_agents() -> dict[str, bool]:
    home = Path.home()
    claude_settings = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "settings.json"
    return {
        "claude_code": claude_settings.exists() or (home / ".claude").exists(),
        "codex": (home / ".codex").exists() or (home / ".codex" / "config.toml").exists(),
        "opencode": (home / ".config" / "opencode" / "opencode.json").exists(),
        "cursor": (home / ".cursor").exists(),
    }


def _deepsee_command() -> str:
    """返回 deepsee 可执行命令(优先 console script,否则 python -m)。路径加引号,
    兼容 Windows 含空格路径嵌入 settings.json 的 command 字段。"""
    exe = shutil.which("deepsee") or shutil.which("dsv")
    if exe:
        return f'"{exe}"'
    return f'"{sys.executable}" -m deepsee'


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".deepsee.bak")
    shutil.copy2(path, bak)
    return bak


# ---------------------------------------------------------------------------
# Claude Code Hook
# ---------------------------------------------------------------------------


def _claude_settings_path() -> Path:
    home = Path.home()
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    return base / "settings.json"


def _claude_local_settings_path() -> Path:
    home = Path.home()
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    return base / "settings.local.json"


def _cc_switch_active() -> bool:
    """检测本机是否在用 cc-switch 管理 Claude Code 供应商(它整文件重写 settings.json)。"""
    if (Path.home() / ".cc-switch" / "cc-switch.db").exists():
        try:
            env = read_json_file(_claude_settings_path(), {}).get("env", {})
            base = env.get("ANTHROPIC_BASE_URL", "")
            if "127.0.0.1" in base or "localhost" in base:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def install_claude_hook() -> bool:
    """把 Hook 注册到 settings.local.json —— cc-switch 只重写 settings.json,
    放 local 才能存活。同时把旧的(在 settings.json 里的)deepsee hook 迁移走。"""
    _migrate_hook_from_settings_json()
    path = _claude_local_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json_file(path, {})
    _backup(path)

    cmd = f"{_deepsee_command()} hook"
    hooks = existing.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    for e in entries:
        for h in e.get("hooks", []):
            if "deepsee" in h.get("command", "") and h.get("command", "").endswith("hook"):
                _set_hook_enabled(True)
                cprint("  · Claude Code Hook 已存在,跳过", "cyan")
                if _cc_switch_active():
                    cprint("  ⚠ 检测到 cc-switch:它会整文件重写 settings.json,但 hook 已放在 settings.local.json,不受影响", "cyan")
                return True
    entries.append({
        "matcher": "Read",
        "hooks": [{"type": "command", "command": cmd, "timeout": 60}],
    })
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # 开启配置里的 hook 标志(hook 运行时检查,false 则透传)
    _set_hook_enabled(True)
    cprint(f"  ✓ 已注册 Claude Code Hook → {path}(settings.local.json,cc-switch 重写 settings.json 也不会丢)", "green")
    return True


def _set_hook_enabled(enabled: bool) -> None:
    cfg = load()
    if cfg["agents"]["claude_code"].get("hook") != enabled:
        cfg["agents"]["claude_code"]["hook"] = enabled
        save(cfg)


def _migrate_hook_from_settings_json() -> bool:
    """把 settings.json 里旧的 deepsee hook 移除(hook 已改放 settings.local.json)。"""
    path = _claude_settings_path()
    data = read_json_file(path, {})
    entries = data.get("hooks", {}).get("PreToolUse", [])
    kept = []
    removed = False
    for e in entries:
        cmds = [h.get("command", "") for h in e.get("hooks", [])]
        if any("deepsee" in c and c.endswith("hook") for c in cmds):
            removed = True
            continue
        kept.append(e)
    if removed:
        _backup(path)
        data.setdefault("hooks", {})["PreToolUse"] = kept
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        cprint("  · 已从 settings.json 迁移旧 deepsee Hook → settings.local.json", "cyan")
    return removed


def uninstall_claude_hook() -> bool:
    removed_any = False
    for path in (_claude_local_settings_path(), _claude_settings_path()):
        data = read_json_file(path, {})
        entries = data.get("hooks", {}).get("PreToolUse", [])
        kept = []
        removed = False
        for e in entries:
            cmds = [h.get("command", "") for h in e.get("hooks", [])]
            if any("deepsee" in c for c in cmds):
                removed = True
                continue
            kept.append(e)
        if removed:
            _backup(path)
            data.setdefault("hooks", {})["PreToolUse"] = kept
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            removed_any = True
    if removed_any:
        _set_hook_enabled(False)
        cprint("  ✓ 已移除 Claude Code Hook(settings.json + settings.local.json)", "green")
    return removed_any


# ---------------------------------------------------------------------------
# Codex Skill
# ---------------------------------------------------------------------------

SKILL_MD = """---
name: deepsee
description: 当用户想让模型"看"图片(粘贴的截图、本地图片路径、图片 URL)而当前模型是 DeepSeek 等纯文本模型时,用 deepsee 把图片转成文字描述。其他情况不要使用。
---

# deepsee 看图

模型无法直接看到图片。需要理解图片时:

1. 运行 `deepsee see <路径或URL>` 获取描述。若用户粘贴了图片,可省略参数:`deepsee see`(自动取最近一张)。
2. 提取图中文字用 `deepsee ocr <路径或URL>`。
3. 想生成图片用 `deepsee gen "提示词"`。
4. 查看状态用 `deepsee status`。

描述返回后会附带识别所用的视觉供应商,直接采用返回的文字即可继续任务。
"""


def install_codex_skill() -> bool:
    skill_dir = Path.home() / ".codex" / "skills" / "deepsee"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    cprint(f"  ✓ 已安装 Codex skill → {skill_dir}", "green")
    return True


def uninstall_codex_skill() -> bool:
    d = Path.home() / ".codex" / "skills" / "deepsee"
    if d.exists():
        shutil.rmtree(d)
        cprint("  ✓ 已移除 Codex skill", "green")
        return True
    return False


# ---------------------------------------------------------------------------
# MCP 注册
# ---------------------------------------------------------------------------


def install_mcp() -> bool:
    """注册 MCP 到 Claude Code 用户配置(不写项目级 .mcp.json,避免作用域不确定)。"""
    import shlex

    cmd = f"{_deepsee_command()} mcp"
    argv = shlex.split(cmd)  # 正确处理引号内的含空格路径
    claude = shutil.which("claude")
    if claude:
        try:
            r = subprocess.run(
                [claude, "mcp", "add", "--scope", "user", "deepsee", "--", *argv],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if r.returncode == 0:
                cprint("  ✓ 已注册 MCP(用户级)→ deepsee", "green")
                return True
            err = (r.stderr or "") + (r.stdout or "")
            if "already exists" in err.lower():
                cprint("  ✓ MCP(deepsee)已在用户配置中,无需重复注册", "green")
                return True
            cprint(f"  ⚠ claude mcp add 失败({err.strip()[:120]})", "yellow")
            cprint("    请手动运行: claude mcp add --scope user deepsee -- <deepsee路径> mcp", "cyan")
        except FileNotFoundError:
            pass
    return False


# ---------------------------------------------------------------------------
# OpenCode 补丁
# ---------------------------------------------------------------------------


def patch_opencode(cfg: dict, proxy_port: int = 8080) -> bool:
    path = Path.home() / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = read_json_file(path, {})
    _backup(path)
    up = cfg.get("upstream", {})
    model = up.get("model", "deepseek-v4-flash")
    # 与运行时 _check_auth 保持一致:master_key 为空 = 不鉴权
    master = cfg.get("proxy", {}).get("master_key", "")
    providers = data.setdefault("provider", {})
    providers["deepsee"] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "deepsee (DeepSeek + vision)",
        "options": {
            "baseURL": f"http://localhost:{proxy_port}/v1",
            "apiKey": master,
        },
        "models": {"deepsee": {"name": model}},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cprint(f"  ✓ 已补丁 OpenCode → {path}", "green")
    return True


def unpatch_opencode() -> bool:
    path = Path.home() / ".config" / "opencode" / "opencode.json"
    data = read_json_file(path, {})
    if not data:
        return False
    _backup(path)
    if "deepsee" in data.get("provider", {}):
        del data["provider"]["deepsee"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        cprint("  ✓ 已移除 OpenCode 补丁", "green")
        return True
    return False


# ---------------------------------------------------------------------------
# 回退 / 撤销
# ---------------------------------------------------------------------------


def restore_backups() -> list[str]:
    """从 .deepsee.bak 备份恢复被修改的配置文件(安装向导/补丁回退)。

    安装时每次修改 settings.json / opencode.json 前都会先写 .deepsee.bak,
    这里把它们整体还原到安装前的状态。
    """
    restored: list[str] = []
    candidates = [
        _claude_settings_path(),
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    for path in candidates:
        bak = Path(str(path) + ".deepsee.bak")
        if bak.exists():
            try:
                shutil.copy2(bak, path)
                restored.append(str(path))
            except OSError:
                pass
    return restored


def uninstall_mcp() -> bool:
    """尝试移除 MCP 注册(优先 claude mcp remove,兜底清 .mcp.json)。"""
    claude = shutil.which("claude")
    if claude:
        try:
            subprocess.run([claude, "mcp", "remove", "--scope", "user", "deepsee"],
                           capture_output=True, timeout=30)
            cprint("  ✓ 已移除 MCP 注册(用户级)", "green")
        except FileNotFoundError:
            pass
    mcp_file = Path.cwd() / ".mcp.json"
    if mcp_file.exists():
        data = read_json_file(mcp_file, {})
        if "deepsee" in data.get("mcpServers", {}):
            del data["mcpServers"]["deepsee"]
            mcp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            cprint("  ✓ 已清理 .mcp.json", "green")
    return True


# ---------------------------------------------------------------------------
# 向导
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def _pick_provider(providers: dict) -> tuple[str, dict]:
    """交互选择供应商,返回 (provider名, 条目)。"""
    names = list(providers.keys())
    cprint("\n可选视觉供应商(全部为 OpenAI 兼容接口,可自接):", "cyan", bold=True)
    for i, name in enumerate(names, 1):
        p = providers[name]
        fee = p.get("fee", "") and f" [{p['fee']}]"
        note = p.get("note", "") and f" — {p['note']}"
        cprint(f"  {i}. {p.get('label')}{fee}{note}")
    choice = _ask(f"选择(1-{len(names)}, 默认 1)", "1")
    try:
        idx = int(choice) - 1
        name = names[max(0, min(idx, len(names) - 1))]
    except ValueError:
        name = names[0]
    entry = deepcopy(providers[name])
    entry["provider"] = name
    return name, entry


def _read_secret(prompt: str, default: str) -> str:
    """读取 api key:优先默认值/环境变量,交互时用 getpass 隐藏输入。"""
    val = _ask(prompt, default)
    if val:
        return val
    if sys.stdin.isatty():
        try:
            return getpass.getpass(prompt + ": ")
        except (EOFError, KeyboardInterrupt):
            return ""
    return ""


def _confirm(prompt: str, default: str = "y") -> bool:
    val = _ask(prompt, default).lower()
    return val in ("y", "yes", "")


def setup_wizard(cfg: dict | None = None) -> int:
    from copy import deepcopy

    from .config import NO_KEY_PROVIDERS

    base = cfg or load()
    plan = deepcopy(base)
    cprint(f"\n  deepsee v{__version__} — 安装向导", "magenta", bold=True)
    cprint("  " + "=" * 46, "magenta")
    cprint("  没有 API key?选「演示模式」或「本地 OCR」可零 key 先用,之后随时补。", "cyan")

    # 1) 视觉供应商
    cprint("\n[1/5] 视觉供应商(用于把图片转成文字):", "green", bold=True)
    name, entry = _pick_provider(PROVIDERS)
    env_key = os.environ.get("DEEPSEE_VISION_API_KEY", "")
    if name in NO_KEY_PROVIDERS:
        base_url, model, key = "", "", ""
        cprint(f"  ✓ {name} 不需要 API key,可直接使用", "cyan")
    elif name == "custom":
        base_url = _ask("  base_url (OpenAI 兼容端点)", entry.get("base_url", ""))
        model = _ask("  model 名称", entry.get("model", ""))
        key = _ask("  api_key", env_key)
    else:
        base_url = entry.get("base_url", "")
        model = entry.get("model", "")
        key = _read_secret(f"  {name} 的 api_key", env_key)
    plan.setdefault("vision", {})["chain"] = [{
        "provider": name, "base_url": base_url, "model": model,
        "api_key": key, "timeout": 60,
        "max_tokens": entry.get("max_tokens", 2048),
    }]
    plan["vision"]["mode"] = _ask("  识图模式(describe/ocr/qa)", "describe")

    # 2) 上游(代理/对话用)
    cprint("\n[2/5] 上游 DeepSeek(供代理与网页对话转发,纯识图不需要):", "green", bold=True)
    up = plan.setdefault("upstream", {})
    up["api_key"] = _ask("  DeepSeek api_key(可留空,之后在面板补)", up.get("api_key", "")) or ""
    up["model"] = _ask("  模型名", up.get("model", "deepseek-v4-flash"))
    up["base_url"] = _ask("  base_url", up.get("base_url", "https://api.deepseek.com/v1"))
    up["format"] = _ask("  上游协议格式(openai/anthropic)", up.get("format", "openai"))

    # 3) 代理 & 面板端口
    cprint("\n[3/5] 服务端口:", "green", bold=True)
    plan["proxy"]["port"] = int(_ask("  代理端口(OpenAI/Anthropic 双协议)", str(plan["proxy"].get("port", 8080))) or 8080)
    plan["web"]["port"] = int(_ask("  Web 面板端口", str(plan["web"].get("port", 8090))) or 8090)

    # 4) 文生图
    cprint("\n[4/5] 文生图后端(可选,让 DeepSeek 能画图):", "green", bold=True)
    gname, gentry = _pick_provider(GEN_PROVIDERS)
    gen = plan.setdefault("generation", {})
    gen["provider"] = gname
    gen["base_url"] = _ask("  base_url", gentry.get("base_url", ""))
    gen["model"] = _ask("  模型名", gentry.get("model", ""))
    gen["api_key"] = _ask("  api_key(留空=不启用画图)", os.environ.get("DEEPSEE_GEN_API_KEY", "")) or ""

    # 5) 集成
    cprint("\n[5/5] 自动集成(探测到):", "green", bold=True)
    agents = detect_agents()
    integrate = plan.setdefault("agents", {})
    if agents.get("claude_code"):
        integrate.setdefault("claude_code", {})["hook"] = _ask("  Claude Code Hook(贴图自动识图)", "y").lower() in ("y", "")
    if agents.get("codex"):
        integrate.setdefault("codex", {})["skill"] = _ask("  Codex skill(装到 ~/.codex/skills)", "y").lower() in ("y", "")
    if agents.get("opencode"):
        integrate.setdefault("opencode", {})["patched"] = _ask("  OpenCode provider 补丁", "n").lower() in ("y", "")
    enable_mcp = _ask("  注册 MCP server(describe_image/generate_image)", "y").lower() in ("y", "")

    # ---- 摘要 + 确认(在此之前不写任何文件) ----
    _print_plan_summary(plan, enable_mcp)
    if not _confirm("\n确认应用以上配置?(n = 不修改任何文件,退出)", "y"):
        cprint("  已取消,未修改任何文件。", "yellow")
        cprint("  想回退之前的安装: `deepsee uninstall`", "cyan")
        return 0

    # ---- 应用 ----
    path = save(plan)
    cprint(f"\n  ✓ 配置已保存 → {path} (0600)", "green")

    if integrate.get("claude_code", {}).get("hook"):
        install_claude_hook()
    if integrate.get("codex", {}).get("skill"):
        install_codex_skill()
    if integrate.get("opencode", {}).get("patched"):
        patch_opencode(plan, plan["proxy"]["port"])
    if enable_mcp:
        install_mcp()

    _verify(plan)

    cprint("\n  下一步:", "magenta", bold=True)
    cprint("    deepsee ui                   # 一键起代理 + 打开聊天界面", "cyan")
    cprint("    deepsee see ./截图.png       # 命令行看图", "cyan")
    cprint("    deepsee gen \"一只柴犬\"      # 文生图", "cyan")
    cprint("    deepsee start / web          # 代理 / 面板", "cyan")
    cprint("    回退安装: deepsee uninstall   # 从备份恢复配置并移除集成", "cyan")
    return 0


def _print_plan_summary(plan: dict, enable_mcp: bool) -> None:
    from .config import NO_KEY_PROVIDERS

    cprint("\n  ── 将应用的配置摘要 ──", "magenta", bold=True)
    chain = plan.get("vision", {}).get("chain", [])
    for i, c in enumerate(chain, 1):
        key = c.get("api_key", "")
        shown = "·" * max(3, len(key) - 4) + key[-4:] if key and c.get("provider") not in NO_KEY_PROVIDERS else "(无需 key)"
        cprint(f"    视觉[{i}] {c.get('provider')} · {c.get('model') or '—'}  key={shown}", "cyan")
    cprint(f"    识图模式: {plan['vision'].get('mode')}", "cyan")
    up = plan.get("upstream", {})
    cprint(f"    上游: {up.get('base_url')} · {up.get('model')} · {'已填 key' if up.get('api_key') else '未填 key(可后补)'}", "cyan")
    cprint(f"    端口: 代理 {plan['proxy'].get('port')} / 面板 {plan['web'].get('port')}", "cyan")
    gen = plan.get("generation", {})
    cprint(f"    文生图: {'已配置 ' + gen.get('model', '') if gen.get('api_key') else '未启用(可后补)'}", "cyan")
    ag = plan.get("agents", {})
    parts = []
    if ag.get("claude_code", {}).get("hook"):
        parts.append("Claude Code Hook")
    if ag.get("codex", {}).get("skill"):
        parts.append("Codex Skill")
    if ag.get("opencode", {}).get("patched"):
        parts.append("OpenCode 补丁")
    if enable_mcp:
        parts.append("MCP")
    cprint(f"    集成: {', '.join(parts) if parts else '(不装任何集成)'}", "cyan")


def _verify(cfg: dict) -> None:
    from .config import NO_KEY_PROVIDERS

    chain = cfg.get("vision", {}).get("chain", [])
    if not chain:
        cprint("\n  ⚠ 未配置视觉链:运行 `deepsee setup` 或编辑 config.json", "yellow")
        return
    missing = [c for c in chain
               if c.get("provider") not in NO_KEY_PROVIDERS and not c.get("api_key")]
    if missing:
        cprint("\n  ⚠ 视觉链缺少 api_key,跳过联网自检(补 key 后 `deepsee setup` 重跑即可)", "yellow")
        return
    cprint("\n  正在自检视觉链路…", "cyan")
    try:
        from .vision import VisionChain

        vc = VisionChain(chain, timeout=cfg["vision"]["timeout"], max_tokens=cfg["vision"]["max_tokens"])
        text, provider = vc.caption(TINY_PNG_DATA_URI, "回复:ok")
        if text:
            cprint(f"  ✓ 视觉链路正常({provider}),自检通过", "green")
    except Exception as e:  # noqa: BLE001
        cprint(f"  ✗ 自检失败: {str(e)[:160]}", "red")
        cprint("    请检查 api_key / base_url / 网络后重试 `deepsee setup`", "yellow")


def print_banner() -> None:
    cprint(
        r"""
   ____                    _____
  |  _ \  ___  ___  ___   | ____| ___ ___  _ __ ___
  | | | |/ _ \/ _ \/ __|  |  _| / __/ _ \| '_ ` _ \
  | |_| |  __/  __/\__ \  | |__| (_| (_) | | | | | |
  |____/ \___|\___||___/  |_____\___\___/|_| |_| |_|
         让 DeepSeek 能「看」,也能「画」
        """,
        "magenta",
        bold=True,
    )
