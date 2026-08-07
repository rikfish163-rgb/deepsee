# deepsee

> 给 DeepSeek 等**纯文本模型**补上「看图」能力 —— 在 Claude Code / Codex / Cursor / 任意聊天客户端里贴一张图,模型能读懂并回答,不报 unsupported,和原生识图模型一样丝滑。

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![零依赖](https://img.shields.io/badge/deps-stdlib%20only-4fd8e0)](pyproject.toml)

**原理**:DeepSeek 纯文本看不到图,deepsee 用便宜/免费的视觉 API(默认智谱 GLM-4V-Flash)把图转成文字描述,再喂给你的 DeepSeek。视觉供应商和上游 LLM 都**任意自接**,不绑定任何厂商。

```
你贴图 ──→ 视觉 API(免费/便宜)转成文字 ──→ 你的 DeepSeek 读描述回答
```

## 快速开始

```bash
pip install deepsee        # 或: git clone <repo> && ./install.sh
deepsee setup              # 向导:①选视觉供应商(智谱免费) ②填上游 ③集成
deepsee install            # 装进 Claude Code(Hook + MCP),重启即生效
# 然后:Claude Code 里直接贴图,DeepSeek 就能看懂
```

完整的**配置方法**(拿 key、各字段含义、常见组合、排障)见 [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)。

## 没有 API key?

`deepsee setup` 选「演示模式」或「本地 OCR」即可零成本先跑起来,之后随时补 key。

## 六种使用形态

| 形态 | 命令/方式 | 适用 |
|---|---|---|
| **Claude Code Hook** | `deepsee install` | 贴图自动识图,最丝滑 |
| **MCP 工具** | `deepsee install` → `describe_image`/`generate_image` | 模型主动调用 |
| **网页聊天界面** | `deepsee ui` / `deepsee web` | 浏览器里贴图即聊(流式) |
| **双协议代理** | `deepsee start` | Cherry Studio / ChatBox / Cursor / OpenCode / Cline |
| **Codex skill** | `deepsee install --skill` | Codex Desktop / CLI |
| **CLI** | `deepsee see/ocr/gen` | 终端直接看图/画图 |

## 兼容性

- **视觉供应商**:任意 OpenAI 兼容视觉端点 —— 智谱 GLM-4V-Flash(免费)、阿里百炼 qwen-vl、硅基流动、OpenAI、Gemini、Groq、OpenRouter、本地 vLLM/Ollama;多供应商**自动降级链**。
- **上游**:任意 OpenAI / Anthropic 兼容端点 —— DeepSeek 官方、各类中转/网关、本地 Ollama;`format=auto` 时双协议各自流式转发。
- **免 key**:`demo` 演示模式(零网络)、`local-ocr` 本地 OCR(离线)。
- 纯 stdlib,零第三方依赖;Linux / macOS / Windows。

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
tests/ pytest(75 用例,全离线 mock)
```

## 开发

```bash
pip install -e '.[dev]'
pytest
```

## License

MIT License —— 随便用、随便改,欢迎提交 PR 与 issue。
