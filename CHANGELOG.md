# Changelog

本项目遵循语义化版本号。尚未发布到 PyPI 的版本以 GitHub 源码为准。

## 0.2.0 - 2026-08-09

### 新增

- 面向问题的视觉提示：自动识别报错、技术图、图表、UI 与截图任务。
- `deepsee compare` 与 MCP `compare_images`，支持 2–4 张图片对比。
- OpenAI `image_url` 字符串/对象、Responses `input_image`、Anthropic base64/URL 图片输入。
- Ollama `gemma3` 本地视觉预设，以及纯环境变量运行配置。
- MCP `analyze_image` 与无状态发现；旧工具名继续兼容。

### 改进

- 相同图片与问题使用内存缓存；失败供应商进入短暂冷却后自动降级。
- 默认向导缩短为三步，高级端口与可选文生图配置移至 `--advanced`。
- 代理与 Web 增加请求体上限、JSON 对象校验及更严格的鉴权比较。
- 默认代理端口统一为 `8081`，避免文档、CLI、Web 和安装器之间不一致。

### 安全

- 环境变量中的 API key 仅作运行时覆盖，不会被 `save()` 写回配置文件。
- 代理与 Web 默认仅监听 `127.0.0.1`；公网监听时要求使用代理主密钥。

## 0.1.0 - 2026-08-07

- 首个公开版本：CLI、Claude Code Hook、MCP、OpenAI/Anthropic 双协议代理与 Web 面板。
