"""CLI 入口:deepsee / dsv。"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .caption import Seer
from .config import GEN_PROVIDERS, PROVIDERS, load, save, validate
from .util import config_home, cprint, mask_secrets, read_json_file


def _pidfile() -> Path:
    return config_home() / "services.pid"


def _write_pid(name: str, pid: int) -> None:
    p = _pidfile()
    data = read_json_file(p, {})
    data[name] = pid
    p.write_text(json.dumps(data))


def _read_pid(name: str) -> int | None:
    p = _pidfile()
    if not p.exists():
        return None
    return read_json_file(p, {}).get(name)


def _is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def _cmd_setup(args) -> int:
    from .installer import setup_wizard

    return setup_wizard()


def _cmd_see(args) -> int:
    cfg = load()
    seer = Seer.from_config(cfg)
    if not cfg.get("vision", {}).get("chain"):
        cprint("未配置视觉 API,请先运行 `deepsee setup`", "red")
        return 1
    source = args.source
    mode = args.mode or ""
    if args.latest and not source:
        from .util import latest_session_image

        p = latest_session_image()
        if not p:
            cprint("未在会话日志中找到图片", "yellow")
            return 1
        source = str(p)
        cprint(f"  从会话日志取到: {p}", "cyan")
    if not source:
        cprint("请提供图片路径/URL,或用 --latest 取最近一张,或先截图", "yellow")
        return 1
    try:
        text, provider = seer.describe(source, mode=mode, question=args.question, verbose=args.verbose)
    except Exception as e:  # noqa: BLE001
        cprint(f"识别失败: {e}", "red")
        return 1
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        cprint(f"已保存描述 → {args.output}", "green")
    else:
        cprint(f"── [{provider}] ──", "cyan")
        print(text)
    return 0


def _cmd_gen(args) -> int:
    cfg = load()
    from .generate import GenError, ImageGenClient

    gen = cfg.get("generation", {})
    if not gen.get("api_key") or not gen.get("base_url"):
        cprint("文生图未配置,请运行 `deepsee setup`(第 4 步填文生图后端)", "red")
        return 1
    client = ImageGenClient.from_config(cfg, size=args.size)
    try:
        path = client.generate(args.prompt)
    except GenError as e:
        cprint(f"生成失败: {e}", "red")
        return 1
    cprint(f"✓ 图片已保存 → {path}", "green")
    return 0


def _cmd_start(args) -> int:
    cfg = load()
    from .proxy import serve_forever

    port = args.port or cfg["proxy"].get("port", 8080)
    host = args.host or "127.0.0.1"
    if args.daemon:
        return _daemonize("proxy", [sys.executable, "-m", "deepsee", "start", "--port", str(port)])
    return serve_forever(host, int(port), cfg)


def _cmd_stop(args) -> int:
    pid = _read_pid(args.name or "proxy")
    if not pid or not _is_alive(pid):
        cprint(f"{args.name or 'proxy'} 未在运行", "yellow")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(20):
        if not _is_alive(pid):
            break
        time.sleep(0.1)
    cprint(f"已停止 {args.name or 'proxy'} (pid {pid})", "green")
    return 0


def _daemonize(name: str, cmd: list[str]) -> int:
    pid = _read_pid(name)
    if pid and _is_alive(pid):
        cprint(f"{name} 已在运行 (pid {pid})", "yellow")
        return 0
    log = config_home() / f"{name}.log"
    with log.open("ab") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=True, cwd=str(Path.home()))
    _write_pid(name, proc.pid)
    time.sleep(1.0)
    if proc.poll() is not None:
        cprint(f"{name} 启动失败,查看日志: {log}", "red")
        return 1
    cprint(f"✓ {name} 已后台启动 (pid {proc.pid}),日志 → {log}", "green")
    return 0


def _cmd_web(args) -> int:
    cfg = load()
    from .web import serve_forever as web_serve

    port = args.port or cfg["web"].get("port", 8090)
    host = args.host or "127.0.0.1"
    if args.daemon:
        return _daemonize("web", [sys.executable, "-m", "deepsee", "web", "--port", str(port)])
    return web_serve(host, int(port), cfg)


def _cmd_status(args) -> int:
    cfg = load()
    problems = validate(cfg)
    cprint(f"\n  deepsee v{__version__}", "magenta", bold=True)
    cprint("  " + "-" * 46)

    chain = cfg.get("vision", {}).get("chain", [])
    cprint("\n  视觉链路:", "cyan", bold=True)
    if chain:
        from copy import deepcopy

        shown = deepcopy(chain)
        mask_secrets(shown)
        for c in shown:
            cprint(f"    {c.get('provider','?')}: {c.get('model','?')}  key={c.get('api_key','')}")
    else:
        cprint("    (未配置)", "yellow")

    up = cfg.get("upstream", {})
    cprint("\n  上游(代理转发):", "cyan", bold=True)
    cprint(f"    {up.get('base_url','?')} · {up.get('model','?')} · {up.get('format','openai')}")

    gen = cfg.get("generation", {})
    cprint("\n  文生图:", "cyan", bold=True)
    cprint(f"    {'✓ 已配置 ' + gen.get('model','') if gen.get('api_key') else '(未配置)'}")

    cprint("\n  服务:", "cyan", bold=True)
    for name, port in (("proxy", cfg["proxy"]["port"]), ("web", cfg["web"]["port"])):
        alive = _is_alive(_read_pid(name) or -1)
        cprint(f"    {name:<6} http://127.0.0.1:{port}  {'● 运行中' if alive else '○ 未运行'}", "green" if alive else "dim")

    cprint("\n  集成:", "cyan", bold=True)
    from .installer import detect_agents

    agents = detect_agents()
    for k, v in agents.items():
        cprint(f"    {k:<12} {'✓ 已探测' if v else '— 未探测'}", "green" if v else "dim")

    cprint("\n  配置问题:", "cyan", bold=True)
    if problems:
        for p in problems:
            cprint(f"    ✗ {p}", "red")
    else:
        cprint("    ✓ 全部就绪", "green")
    return 1 if problems else 0


def _cmd_config(args) -> int:
    cfg = load()
    action = args.action or "show"
    if action == "show":
        from copy import deepcopy

        safe = deepcopy(cfg)
        mask_secrets(safe)
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0
    if action == "set":
        return _config_set(cfg, args.key, args.value)
    if action == "providers":
        for name, p in PROVIDERS.items():
            cprint(f"  {name:<12} {p.get('label')}", "cyan")
        cprint("\n  文生图后端:", "cyan")
        for name, p in GEN_PROVIDERS.items():
            cprint(f"  {name:<12} {p.get('label')}", "cyan")
        return 0
    return 1


def _config_set(cfg: dict, key: str, value: str) -> int:
    """支持点路径,如 vision.chain[0].model。值按 JSON 解析,失败按字符串。"""
    parts = key.replace("[", ".").replace("]", "").split(".")
    node = cfg
    try:
        for p in parts[:-1]:
            idx = int(p) if p.isdigit() else None
            node = node[idx if idx is not None else p]
        last = parts[-1]
        if last.isdigit():
            node[int(last)] = _parse_val(value)
        else:
            node[last] = _parse_val(value)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        cprint(f"设置失败: {e}", "red")
        return 1
    save(cfg)
    cprint(f"✓ 已更新 {key} = {value}", "green")
    return 0


def _parse_val(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.startswith(("[", "{")):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


def _cmd_hook(args) -> int:
    from .hook_claude import main as hook_main

    return hook_main()


def _cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main

    return mcp_main()


def _cmd_install(args) -> int:
    """把集成装到本机(用现有配置,非交互):Claude Code Hook + MCP,可加 Codex/OpenCode。"""
    from .installer import (
        install_claude_hook,
        install_codex_skill,
        install_mcp,
        patch_opencode,
    )

    cfg = load()
    cprint("安装 deepsee 集成…", "cyan")
    if args.hook or args.all:
        install_claude_hook()
    if args.mcp or args.all:
        install_mcp()
    if args.skill:
        install_codex_skill()
    if args.opencode:
        patch_opencode(cfg, cfg["proxy"]["port"])
    cprint("\n完成。**重启 Claude Code** 后生效;回退用 `deepsee uninstall`", "green", bold=True)
    return 0


def _cmd_uninstall(args) -> int:
    from .installer import (
        restore_backups,
        uninstall_codex_skill,
        uninstall_mcp,
    )

    cprint("正在回退安装…", "cyan")
    restored = restore_backups()
    for p in restored:
        cprint(f"  ✓ 已从备份恢复 {p}", "green")
    uninstall_codex_skill()
    uninstall_mcp()
    p = config_home() / "services.pid"
    if p.exists():
        p.unlink()
    if not restored:
        cprint("  (未发现需要恢复的备份)", "dim")
    cprint("已回退。~/.deepsee/config.json 保留,可手动删除;要清掉重来运行 `deepsee setup`", "cyan")
    return 0


def _cmd_client_cfg(args) -> int:
    cfg = load()
    up = cfg.get("upstream", {})
    port = cfg["proxy"].get("port", 8080)
    master = cfg["proxy"].get("master_key", "")
    base = f"http://127.0.0.1:{port}/v1"
    model = up.get("model", "deepseek-v4-flash")
    key = master or "(留空,无需鉴权)"

    cprint(f"\n  deepsee 代理接入配置(先运行 `deepsee start`)", "magenta", bold=True)
    cprint(f"    base_url : {base}", "cyan")
    cprint(f"    api_key  : {key}", "cyan")
    cprint(f"    model    : {model}", "cyan")

    cprint("\n  Cherry Studio / ChatBox / NextChat / Cline 通用设置:", "cyan")
    cprint(f"    API 地址 = {base}", "cyan")
    cprint(f"    API 密钥 = {key}", "cyan")
    cprint(f"    模型     = {model}", "cyan")

    cprint("\n  Claude Code(Anthropic 协议)指向代理:", "cyan")
    cprint(f"    设置 ANTHROPIC_BASE_URL=http://127.0.0.1:{port} 即可(或用 `deepsee setup` 装 Hook,贴图更顺滑)", "cyan")
    return 0


def _cmd_ui(args) -> int:
    """一键:起代理 + 起面板 + 打开浏览器。"""
    cfg = load()
    proxy_port = cfg["proxy"].get("port", 8080)
    web_port = cfg["web"].get("port", 8090)
    _daemonize("proxy", [sys.executable, "-m", "deepsee", "start", "--port", str(proxy_port)])
    _daemonize("web", [sys.executable, "-m", "deepsee", "web", "--port", str(web_port)])
    _open_browser(f"http://127.0.0.1:{web_port}")
    cprint(f"\n  面板已打开 → http://127.0.0.1:{web_port}", "green", bold=True)
    cprint(f"  代理 → http://127.0.0.1:{proxy_port}/v1  (停止: `deepsee stop proxy`)", "cyan")
    return 0


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deepsee",
        description="给 DeepSeek 等纯文本模型补齐看图/画图能力",
    )
    p.add_argument("--version", action="version", version=f"deepsee {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("setup", help="安装向导(配置视觉/上游/集成)")
    s.set_defaults(fn=_cmd_setup)

    s = sub.add_parser("see", help="看图:图片 → 文字描述")
    s.add_argument("source", nargs="?", help="图片路径/URL(留空需配合 --latest 或先截图)")
    s.add_argument("-m", "--mode", choices=["describe", "ocr", "qa"])
    s.add_argument("-q", "--question")
    s.add_argument("--latest", action="store_true", help="从 Claude Code/Codex 会话日志取最近图片")
    s.add_argument("-o", "--output", help="描述写入文件")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=_cmd_see)

    s = sub.add_parser("ocr", help="提取图片文字")
    s.add_argument("source", nargs="?", default="")
    s.add_argument("--latest", action="store_true")
    s.set_defaults(fn=_cmd_see, mode="ocr")

    s = sub.add_parser("gen", help="文生图")
    s.add_argument("prompt")
    s.add_argument("-s", "--size", default="")
    s.set_defaults(fn=_cmd_gen)

    s = sub.add_parser("start", help="启动双协议代理")
    s.add_argument("-p", "--port", type=int, default=0)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("-d", "--daemon", action="store_true")
    s.set_defaults(fn=_cmd_start)

    s = sub.add_parser("stop", help="停止后台服务")
    s.add_argument("name", nargs="?", default="proxy", choices=["proxy", "web"])
    s.set_defaults(fn=_cmd_stop)

    s = sub.add_parser("web", help="打开 Web 面板")
    s.add_argument("-p", "--port", type=int, default=0)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("-d", "--daemon", action="store_true")
    s.set_defaults(fn=_cmd_web)

    s = sub.add_parser("ui", help="一键启动代理 + 面板并打开浏览器")
    s.set_defaults(fn=_cmd_ui)

    s = sub.add_parser("install", help="安装集成到本机(Claude Code Hook + MCP)")
    s.add_argument("--all", action="store_true", default=True, help="安装 Hook + MCP(默认)")
    s.add_argument("--hook", action="store_true")
    s.add_argument("--mcp", action="store_true")
    s.add_argument("--skill", action="store_true", help="顺带装 Codex skill")
    s.add_argument("--opencode", action="store_true", help="顺带打 OpenCode 补丁")
    s.set_defaults(fn=_cmd_install)

    s = sub.add_parser("client-cfg", help="打印接入其他聊天客户端的配置")
    s.set_defaults(fn=_cmd_client_cfg)

    s = sub.add_parser("status", help="查看状态")
    s.set_defaults(fn=_cmd_status)

    s = sub.add_parser("config", help="配置管理")
    s.add_argument("action", choices=["show", "set", "providers"], nargs="?")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=_cmd_config)

    s = sub.add_parser("hook", help="(内部)Claude Code Hook 入口")
    s.set_defaults(fn=_cmd_hook)

    s = sub.add_parser("mcp", help="(内部)MCP Server 入口")
    s.set_defaults(fn=_cmd_mcp)

    s = sub.add_parser("uninstall", help="卸载集成")
    s.set_defaults(fn=_cmd_uninstall)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not getattr(args, "fn", None):
        build_parser().print_help()
        return 0
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        cprint("\n已中断", "yellow")
        return 130


if __name__ == "__main__":
    sys.exit(main())
