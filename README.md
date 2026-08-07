# deepsee

> 给 DeepSeek 等**纯文本模型**补齐「看」与「画」能力的全家桶。

DeepSeek V4 / V3 是纯文本模型——不能看图,也不能画图。`deepsee` 用廉价的
OpenAI 兼容视觉 API(默认免费的 GLM-4V-Flash)把图片转成文字喂给 DeepSeek,
再用任意文生图 API 让 DeepSeek 能"画出"结果。全程支持**自己接任意 API**。

## 一句话

```bash
pip install . && deepsee setup        # 向导:填一次视觉 API key,自动配好各终端
deepsee see ./截图.png                # 命令行看图
deepsee gen "一只在月球上打篮球的柴犬"   # 命令行画图
deepsee web                           # 打开美观的 Web 面板(测试/配置/状态)
```

## 它解决了什么

| 痛点 | 现状 |
|---|---|
| 在 Claude Code 里贴图,DeepSeek 看不懂 | `PreToolUse` Hook 自动把图换成文字描述,模型以为自己看了图 |
| Codex / Cursor / OpenCode 里发图直接报错崩溃 | 本地代理 `base_url` 一改,image 块自动转文字 |
| 想给 DeepSeek 加图片能力但不会写 MCP | 内置手写轻量 MCP server(`describe_image` / `generate_image`) |
| 图想存/想画但没接口 | `deepsee gen` + Web 面板一键文生图 |

## 兼容矩阵

| 载体 | 集成方式 | 状态 |
|---|---|---|
| Claude Code | Hook + MCP + 代理(Anthropic 协议) | ✅ 自动配置 |
| Codex Desktop / CLI | Skill + 代理(OpenAI 协议) | ✅ 自动配置 |
| OpenCode | provider 补丁 | ✅ 自动配置 |
| Cursor | base_url 指引 | ✅ 自动配置 |
| 任意 OpenAI / Anthropic 客户端 | 双协议代理 | ✅ |

## 视觉供应商(可自接任意 OpenAI 兼容)

内置预设:智谱 GLM-4V-Flash(**免费**)、阿里百炼 qwen-vl、硅基流动 Qwen2.5-VL、
OpenAI、Gemini、Groq、OpenRouter、StepFun、Moonshot、腾讯混元、零一万物、本地 vLLM/Ollama。

多供应商**降级链**:第一个挂了自动切下一个。

## 目录

- `deepsee/` 核心包(零第三方依赖,纯 stdlib)
- `deepsee/web/` Web 面板
- `tests/` pytest 测试
- `docs/` PRD / 架构文档

## 开发

```bash
pip install -e '.[dev]'
pytest
```
