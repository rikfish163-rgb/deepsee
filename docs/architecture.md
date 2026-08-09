# 架构设计 — deepsee

## 总览

```
                     ┌────────────────────────────────────────────┐
                     │              deepsee 核心 (stdlib)         │
  用户/Agent          │                                            │
 ┌────────────┐      │   config ── vision(降级链) ── caption      │
 │ Claude Code│──Hook│        │        │                           │
 │ Codex      │──Skill│        ▼        ▼                          │
 │ OpenCode   │──patch│   generate ── 文生图 API                   │
 │ 通用客户端  │──proxy│        │                                   │
 │ 终端 CLI   │──see  │        └── proxy(OpenAI/Anthropic 双协议)  │
 │ Web 面板   │──web  │                     │                      │
 └────────────┘      │                     ▼                      │
                     │              DeepSeek 上游 (文本)           │
                     └────────────────────────────────────────────┘
```

所有「图片 → 文字」都走同一个 `vision.VisionChain`(多供应商降级链),
所有「文字 → 图片」都走 `generate.ImageGenClient`。**核心零第三方依赖**,纯 `urllib`/`http.server`。

## 模块职责

| 模块 | 职责 |
|---|---|
| `config.py` | `~/.deepsee/config.json` 读写、供应商预设、深层合并、mtime 缓存校验 |
| `util.py` | 图片判定/base64/缩放、跨平台剪贴板截图、会话日志增量捞图、HTTP+SSE、`mask_secrets`、ANSI 输出 |
| `vision.py` | `VisionClient`(OpenAI 兼容 chat)+ `VisionChain`(降级链) |
| `caption.py` | 三种识图模式(describe/ocr/qa)的 prompt 与调用、`Seer.from_config` |
| `chat.py` | 对话服务:`Converter`(图片→文字)、`chat_openai`/`iter_chat`(流式)。**代理与 Web 面板共用同一入口** |
| `generate.py` | OpenAI 兼容 `/images/generations` 客户端 + 后端预设 |
| `proxy.py` | `ThreadingHTTPServer`;`/v1/chat/completions`、`/v1/messages`、`/v1/models`、`/v1/images/generations`、`/health` |
| `hook_claude.py` | Claude Code PreToolUse(Read)拦截:图片 → 临时 txt → 改写 file_path |
| `mcp_server.py` | 手写 stdio JSON-RPC MCP:`analyze_image/compare_images/ocr_image/vision_status`(保留 describe/generate 兼容) |
| `cli.py` | argparse 子命令入口 |
| `installer.py` | `setup` 向导:探测终端、写配置、备份回滚、注册 Hook/Skill/patch、联网自检 |
| `web.py` + `web/static/` | stdlib http.server + 单页前端(状态/识图台/画图台/配置) |

## 关键流程

### 1. 识图桥接(代理 / Hook 共用)

```
image_url / image 块或图片路径
   → VisionChain.caption(data, mode, question)
   → "[Image N] <详细文字描述>"
   → 替换原图片块 → 纯文本请求 → DeepSeek
```

### 2. 降级链

`config.vision.chain = [{provider, api_key, ...}, ...]`,依次尝试;
失败(网络/4xx/5xx/空输出)→ 下一个;全失败 → 抛 `VisionError`,调用方决定透传原图块或报错。

### 3. 代理协议

- **同协议流式透传**:OpenAI→OpenAI、Anthropic→Anthropic。只改请求(图片→文字),响应 SSE 原样转发。
- **跨协议**:仅非流式,请求/响应格式转换(转换器在 `proxy.py`)。
- 上游默认 DeepSeek:OpenAI 协议 `https://api.deepseek.com/v1`、Anthropic 协议 `https://api.deepseek.com/anthropic`。
- 可选 `MASTER_API_KEY`:未设置则不鉴权(本地默认)。

### 4. Hook 安全网

任何异常(缺 key、视觉 API 挂了、文件不是图)→ 原样输出 stdin 输入,**绝不阻断 Read**。

### 5. 会话捞图(see --latest)

- Claude Code:`~/.claude/projects/*/*.jsonl` 中 `image_path` / `mime_type` 块
- Codex:`~/.codex/sessions/*.jsonl` 中 base64 图片
找到最近一张 → 解码为临时文件 → 识图。

## 配置示例

```jsonc
{
  "vision": {
    "chain": [ { "provider": "zhipu", "api_key": "sk-..." } ],
    "mode": "describe", "max_tokens": 2048, "timeout": 60
  },
  "upstream": {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "...", "model": "deepseek-v4-flash", "format": "openai"
  },
  "proxy": { "port": 8081, "master_key": "", "max_body_mb": 25 },
  "generation": { "provider": "siliconflow", "api_key": "...",
                  "model": "black-forest-labs/FLUX.1-schnell-free" },
  "web": { "port": 8090 },
  "agents": { "claude_code": { "hook": true }, "codex": { "skill": true },
              "opencode": { "patched": true } }
}
```

## 安全

- 配置目录 `~/.deepsee` 权限 `700`,含 key 的 `config.json` 权限 `600`。
- 代理鉴权键与视觉 key 分离;日志不打印 key。
- 不做任何网页端逆向 / token 窃取。

## 测试策略

- 全部离线:本地 `http.server` mock 上游 / mock 视觉 API。
- `test_config` 配置读写合并;`test_vision` 请求构造与降级;`test_proxy` 双协议图片替换
  与流式透传;`test_mcp` NDJSON 协议会话;`test_util` 工具函数。
