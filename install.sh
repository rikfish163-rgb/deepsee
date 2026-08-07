#!/usr/bin/env bash
# deepsee 一键安装脚本(本地项目路径)
# 用法: ./install.sh   (之后按向导填 API key)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

echo "→ 创建虚拟环境 (.venv)"
"$PY" -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install -q --upgrade pip
echo "→ 安装 deepsee"
"$PROJECT_DIR/.venv/bin/python" -m pip install -q -e "$PROJECT_DIR"

echo "→ 启动安装向导"
"$PROJECT_DIR/.venv/bin/deepsee" setup

echo
echo "完成!常用命令:"
echo "  $PROJECT_DIR/.venv/bin/deepsee status   # 查看状态"
echo "  $PROJECT_DIR/.venv/bin/deepsee web      # Web 面板"
echo "  $PROJECT_DIR/.venv/bin/deepsee start    # 双协议代理"
