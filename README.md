# deepsee

> 给 DeepSeek 等**纯文本模型**补上「看图」能力 —— 在 Claude Code / Codex / Cursor / 任意聊天客户端里贴一张图,模型能读懂并回答,不报 unsupported,和原生识图模型一样丝滑。

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![零依赖](https://img.shields.io/badge/deps-stdlib%20only-4fd8e0)](pyproject.toml)
[![CI](https://github.com/rikfish163-rgb/deepsee/actions/workflows/ci.yml/badge.svg)](https://github.com/rikfish163-rgb/deepsee/actions/workflows/ci.yml)

Public, vendor-neutral vision bridge for text-only LLMs. 中文文档为主，欢迎英文 issue / PR。

**原理**:DeepSeek 纯文本看不到图,deepsee 用便宜/免费的视觉 API(默认智谱 GLM-4V-Flash)把图转成文字描述,再喂给你的 DeepSeek。视觉供应商和上游 LLM 都**任意自接**,不绑定任何厂商。识图提示会带上用户原问题,避免先做一遍泛化描述再让 DeepSeek 猜重点。

```
你贴图 ──→ 视觉 API(免费/便宜)转成文字 ──→ 你的 DeepSeek 读描述回答
```

## 快速开始

```bash
pipx install 'git+https://github.com/rikfish163-rgb/deepsee.git'
deepsee setup                  # 3 步:①视觉 ②DeepSeek ③集成
deepsee install                # 装进 Claude Code(Hook + MCP),重启即生效
# 然后:Claude Code 里直接贴图,DeepSeek 就能看懂
```

不用 pipx 也可运行 `python -m pip install 'git+https://github.com/rikfish163-rgb/deepsee.git'`。当前先从 GitHub 安装;不要使用尚未发布的 PyPI 包名。

完整的**配置方法**(拿 key、各字段含义、常见组合、排障)见 [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)。

## 没有 API key?

`deepsee setup` 选「演示模式」或「本地 OCR」即可零成本先跑起来,之后随时补 key。

## 使用形态

| 形态 | 命令/方式 | 适用 |
|---|---|---|
| **Claude Code 路由代理(主)** | `deepsee route on` | 覆盖所有图片场景:粘贴/headless/Read,协议层转文字 |
| **Claude Code Hook** | `deepsee install` | 交互式贴图/Read 自动识图(写在 settings.local.json,cc-switch 不抹) |
| **MCP 工具** | `deepsee install` → `analyze_image`/`compare_images` | 只靠视觉 key 即可,不依赖对话额度 |
| **网页聊天界面** | `deepsee ui` / `deepsee web` | 浏览器里贴图即聊(流式) |
| **双协议代理** | `deepsee start` | Cherry Studio / ChatBox / Cursor / OpenCode / Cline |
| **Codex skill** | `deepsee install --skill` | Codex Desktop / CLI |
| **CLI** | `deepsee see/ocr/compare` | 终端直接看图、提字、对比图片 |

## 装成 MCP 插件(Cursor / Cline / 任意 MCP 客户端)

仓库里自带 `mcp.json`,在 Cursor(Cursor 设置 → MCP)或 Cline 里指向它即可;
或手动注册(把 `deepsee` 换成 `deepsee mcp` 的实际命令):

```json
{
  "mcpServers": {
    "deepsee": {
      "command": "deepsee",
      "args": ["mcp"]
    }
  }
}
```

- GitHub 安装后直接运行 `deepsee mcp`。
- 推荐工具:`analyze_image` / `ocr_image` / `compare_images` / `vision_status`;`describe_image` 保留兼容,文生图是可选高级能力。
- 同时兼容旧式 `initialize` 与 MCP 2026-07-28 `server/discover`,列表结果带缓存提示。
- 配置目录通过环境变量 `DEEPSEE_HOME` 覆盖(默认 `~/.deepsee`)。
- 发布到 Smithery/mcp.so:见 `docs/CONFIGURATION.md` 的「MCP 发布」一节。

## 兼容性

- **视觉供应商**:任意 OpenAI 兼容视觉端点 —— 智谱 GLM-4V-Flash(免费)、阿里百炼 qwen-vl、硅基流动、OpenAI、Gemini、Groq、OpenRouter、本地 Ollama `gemma3`;多供应商**自动降级链 + 失败冷却**。
- **上游**:任意 OpenAI / Anthropic 兼容端点 —— DeepSeek 官方、各类中转/网关、本地 Ollama;`format=auto` 时双协议各自流式转发。
- **图片输入**:OpenAI `image_url` 对象/字符串、`input_image`,Anthropic base64/URL,本地路径、URL、data URI。
- **任务提示**:报错、技术图、图表、UI、截图自动识别,也可用 `--task` 明确指定;支持 2–4 图对比。
- **省钱与稳定**:相同图片+问题内存缓存、失败后端短暂冷却、请求体大小上限。
- **免 key**:`demo` 演示模式、`local-ocr` 本地 OCR、Ollama 本地视觉。
- 纯 stdlib,零第三方依赖;Linux / macOS / Windows。

容器/CI 可以完全用环境变量启动,无需写配置文件:

```bash
export DEEPSEE_VISION_BASE_URL='http://127.0.0.1:11434/v1'
export DEEPSEE_VISION_MODEL='gemma3'
export DEEPSEE_UPSTREAM_API_KEY='...'
deepsee start
```

安全默认值:代理和 Web 都只监听 `127.0.0.1`;不要把 Web 配置面板直接暴露到公网。若代理必须监听局域网/公网,先设置 `DEEPSEE_PROXY_MASTER_KEY`。

## 项目结构

```
deepsee/
├── vision.py    视觉客户端 + 多供应商降级链
├── caption.py   识图(describe/ocr/qa)
├── chat.py      对话服务(图片→文字→上游,代理与网页共用)
├── proxy.py     OpenAI + Anthropic 双协议代理(图片块→文字→DeepSeek)
├── mcp_server.py 手写轻量 MCP(零依赖)
├── hook_claude.py Claude Code PreToolUse Hook
├── web.py + web/static/  网页聊天界面(标签页:对话/识图/画图/状态/配置)
├── cli.py / installer.py / config.py / util.py / generate.py
docs/  PRD / 架构 / 配置方法
tests/ pytest 全离线 mock，覆盖 CLI / MCP / 双协议代理 / Web
```

## 开发

```bash
pip install -e '.[dev]'
pytest
```

测试不读取真实 `~/.deepsee`，也不需要任何 API key。真实供应商可在本机用
`deepsee see <图片> -q "图里是什么？"` 做最小烟雾测试；请勿把 key、配置文件或响应日志提交到仓库。

发布记录见 [`CHANGELOG.md`](CHANGELOG.md)，贡献约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，安全问题见 [`SECURITY.md`](SECURITY.md)。

## License

MIT License —— 随便用、随便改,欢迎提交 PR 与 issue。
