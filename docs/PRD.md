# PRD — deepsee

**给 DeepSeek 等纯文本模型补齐「看图」能力的零依赖、厂商无关视觉桥接器**

- 版本:0.2 / 日期:2026-08-09 / 作者:hetaisheng

## 1. 背景与问题

DeepSeek 官方 API 是纯文本模型,不能处理 `image_url` / `image` 内容块,也不能生成图片。
用户在 Claude Code / Codex / Cursor / 任意 OpenAI-Anthropic 客户端里使用 DeepSeek 时:

- 贴图 → 请求直接崩溃(`unknown variant image_url`)或模型"看不见";
- 想画图 → 无任何能力。

2026-08 涌现了一批单点方案(deepseek-vision 生态),各有短板:
依赖重、仅单平台、仅单终端、安装繁琐、UI 朴素、或靠抓网页 token(脆弱)。

## 2. 目标(取长补短)

| # | 目标 | 衡量 |
|---|---|---|
| G1 | **兼容性最强** | 覆盖 Claude Code / Codex / OpenCode / Cursor / 通用 OpenAI & Anthropic 客户端;三平台(Linux/macOS/Windows);流式+非流式;多图并行 |
| G2 | **上手最方便** | 一条命令安装;`deepsee setup` 向导自动检测终端、写配置、备份回滚、联网自检 |
| G3 | **UI 美观** | Web 面板含:状态卡片、识图测试台、文生图测试台、配置编辑器 |
| G4 | **便宜可自接** | 默认免费 GLM-4V-Flash;任意 OpenAI 兼容视觉 API;供应商降级链 |
| G5 | **稳** | 只用官方 API(不做 token 破解);核心零第三方依赖;失败自动降级不打断会话 |

## 3. 范围

### 3.1 能力

- **识图(理解)**:图片路径 / URL / 剪贴板截图 / Agent 会话里贴过的图 → 详细文字描述。
  模式:describe(详尽描述)、ocr(原文提取)、qa(回答具体问题)。
- **画图(生成)**:文字 → 图片,支持任选 OpenAI 兼容文生图端点,本地保存。
- **桥接**:在 DeepSeek 前加一层,把图片块替换成 `[Image N] <描述>`,文本原样转发。

### 3.2 形态

1. **CLI** `deepsee see|ocr|gen|setup|start|stop|status|web|config|hook`
2. **Claude Code PreToolUse Hook** — 贴图即自动识图,对用户透明
3. **MCP Server** — `describe_image` / `ocr_image` / `generate_image` / `vision_status`
4. **双协议代理** — `/v1/chat/completions`(OpenAI)+ `/v1/messages`(Anthropic),图片→文字,转发 DeepSeek
5. **Web 面板** — 状态 / 测试 / 配置

### 3.3 不在范围(0.2)

- 不做 DeepSeek 网页 token 抓取(PoW/WASM)——脆弱且违反服务条款
- 不捆绑或自动部署本地 VLM——Ollama `gemma3` 仅作为用户自选后端
- 不做多租户 / 计费

## 4. 用户故事

- 作为 Claude Code 用户:我贴一张报错截图,DeepSeek 直接看懂并帮我改代码,我完全不知道背后经过了转换。
- 作为 Codex 用户:我不用改任何模型配置,`base_url` 指向本地代理即可。
- 作为想画图的人:`deepsee gen "..."` 或面板里输入 prompt 得到图片文件。

## 5. 成功标准

- `pip install . && deepsee setup` 10 分钟内可用(填 key 后)。
- 三种协议路径(OpenAI / Anthropic / MCP)均有自动化测试通过。
- 供应商降级链在首个失败时自动切换。
- Web 面板在无 API key 时也能打开并展示引导。

## 6. 风险

| 风险 | 缓解 |
|---|---|
| 各家视觉 API 返回格式差异 | 统一转 OpenAI 兼容;预设逐项校准;错误信息友好 |
| 流式转发复杂度 | 同协议流式透传(只改请求不改响应);跨协议仅支持非流式 |
| Hook 破坏会话 | 任何异常都原样透传,绝不阻断 Read |
| 无 API key 无法端到端测 | 测试用本地 mock HTTP 服务;联网自检放 setup 向导 |

## 7. 里程碑

- M1 核心库 + 测试(配置/视觉/生成/代理)
- M2 CLI + 向导 + Hook/MCP 接入
- M3 Web 面板
- M4 文档、安装脚本、发布
