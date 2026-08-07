# 贡献指南

欢迎 PR 与 issue!项目很小、零依赖,保持简单。

## 环境

```bash
pip install -e '.[dev]'
pytest          # 全部离线(mock HTTP),提交前必须全绿
```

## 约定

- 核心**零第三方依赖**(纯 stdlib)。可选能力放 `[project.optional-dependencies]`(如 `ocr`、`image`、`mcp`)。
- 新增任何对外行为都要有 pytest。
- 改动说明用常规提交格式:`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`。

## 目录指路

- `vision.py` 降级链、`caption.py` 识图、`chat.py` 对话服务 —— 三条能力主线
- `proxy.py` 双协议代理、`mcp_server.py` 手写 MCP、`hook_claude.py` Hook —— 三种接入
- `web/` 网页聊天界面,前端无框架原生 JS
- `docs/CONFIGURATION.md` 用户配置方法,改动要同步更新

## 别做的事

- 不要在代码里硬编码任何 API key / 供应商凭据
- 不做网页端逆向 / token 窃取(本项目只用官方 API)
