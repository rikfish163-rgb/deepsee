"""CLI 解析回归测试。"""

from deepsee.cli import build_parser


def test_ocr_subcommand_has_required_defaults():
    """ocr 子命令复用 _cmd_see,必须补齐它访问的属性,避免 Namespace 缺属性崩溃。"""
    p = build_parser()
    args = p.parse_args(["ocr", "img.png"])
    assert args.mode == "ocr"
    assert args.question == ""
    assert args.output is None
    assert args.verbose is False
    assert args.latest is False


def test_see_subcommand_flags():
    p = build_parser()
    args = p.parse_args([
        "see", "a.png", "-m", "qa", "-q", "这是什么?", "--task", "diagram",
        "-o", "/tmp/out.txt", "-v",
    ])
    assert args.mode == "qa"
    assert args.question == "这是什么?"
    assert args.task == "diagram"
    assert args.output == "/tmp/out.txt"
    assert args.verbose is True


def test_compare_subcommand_parser():
    p = build_parser()
    args = p.parse_args(["compare", "before.png", "after.png", "-q", "哪里变化了？"])
    assert args.sources == ["before.png", "after.png"]
    assert args.question == "哪里变化了？"
    assert args.output is None


def test_install_individual_flag_not_all():
    """install --mcp 不应连带装 hook(--all 不再是恒真)。"""
    p = build_parser()
    args = p.parse_args(["install", "--mcp"])
    assert args.all is False
    assert args.hook is False
    assert args.mcp is True


def test_route_parser():
    p = build_parser()
    assert p.parse_args(["route", "on"]).mode == "on"
    assert p.parse_args(["route", "off"]).mode == "off"
