# deepsee 配置方法(面向所有用户)

> 目标:让你在 **Claude Code / Codex / Cursor / 任意聊天客户端** 里用 **DeepSeek 或任何纯文本模型** 时,可以"看图"——贴一张图进去,模型能读懂并回答,不报 unsupported,和原生识图模型一样丝滑。

---

## 0. 原理(30 秒看懂)

DeepSeek 等纯文本模型看不到图片。deepsee 做一层"翻译"：

```
你贴图 ──→ 视觉 API(便宜/免费,如智谱 GLM-4V-Flash)转成文字描述 ──→ 你的 DeepSeek 读描述回答
```

所以你需要 **两个东西**：

| 用途 | 需要什么 | 免费方案 | 付费方案 |
|---|---|---|---|
| **视觉**(图→文字) | 任意 OpenAI 兼容视觉 API 的 key | 智谱 GLM-4V-Flash(免费) · 本地 OCR(免 key) | 阿里百炼 qwen-vl / OpenAI |
| **上游**(对话) | 你的 LLM 端点 | — | DeepSeek 官方 / 任意 OpenAI·Anthropic 兼容中继 |

deepsee 对这两者**都不绑定具体厂商**,自己接什么都行。

---

## 1. 安装

```bash
# 当前从 GitHub 安装(PyPI 包尚未发布)
pipx install 'git+https://github.com/rikfish163-rgb/deepsee.git'
# 或:python -m pip install 'git+https://github.com/rikfish163-rgb/deepsee.git'

# 或从源码
git clone https://github.com/rikfish163-rgb/deepsee.git
cd deepsee
./install.sh          # 建 venv + 进安装向导
```

---

## 2. 拿视觉 key(图→文字)

任选其一,拿到一个就够：

- **智谱 GLM-4V-Flash(免费,推荐)** — [open.bigmodel.cn](https://open.bigmodel.cn) 注册 → API 密钥 → 创建。格式 `xxxx.yyyy`。
- **阿里云百炼** — [bailian.console.aliyun.com](https://bailian.console.aliyun.com) 开通 → 新用户免费额度,模型 `qwen-vl-max` / `qwen3-vl-flash`。
- **硅基流动** — [cloud.siliconflow.cn](https://cloud.siliconflow.cn) 新用户送额度,模型 `Qwen/Qwen2.5-VL-72B-Instruct`。
- **不想注册?** — `pip install 'deepsee[ocr]'`,用**本地 OCR**,离线提取图片文字(免费、免 key,但只能提取文字,不能"描述画面")。
- **本地完整识图** — 安装 Ollama 后运行 `ollama pull gemma3`,`deepsee setup` 选择 `Ollama gemma3`,无需 API key。

## 3. 拿上游 LLM key(对话)

任选其一：

- **DeepSeek 官方** — [platform.deepseek.com](https://platform.deepseek.com) 注册 → 创建 API Key(¥10 够用很久)。OpenAI 兼容地址 `https://api.deepseek.com/v1`,Anthropic 兼容 `https://api.deepseek.com/anthropic`。
- **任意 OpenAI 兼容中继/自建**(OpenAI 格式 base_url + key)。
- **任意 Anthropic 兼容中继**(如某些 Claude 兼容网关)。
- **本地 Ollama** — 不花钱,模型填 `qwen2.5-vl` 之类。

---

## 4. 跑快速向导 `deepsee setup`

默认向导只有 3 步,最后给摘要确认。API key 在终端中隐藏输入。

### [1/3] 视觉供应商
选一个,填 key。选 `custom` 可填**任意 OpenAI 兼容视觉端点**(base_url + model + key)。

### [2/3] 上游 DeepSeek(对话/代理转发用)
快速模式只询问 DeepSeek key,其余字段沿用下面的默认值或已有配置;自定义地址/模型/协议用 `deepsee setup --advanced`。

| 字段 | 含义 | DeepSeek 官方填 | 通用中继填 |
|---|---|---|---|
| `base_url` | OpenAI 协议地址(含 `/v1`) | `https://api.deepseek.com/v1` | `<你的中转>/v1` |
| `api_key` | 上游 key | 你的 DeepSeek key | 中继的 key |
| `model` | 模型名 | `deepseek-v4-flash` | 中继支持的模型 |
| `format` | `openai` / `anthropic` / `auto` | `openai` | `auto` |

> `format=auto`:OpenAI 请求→OpenAI 上游、Anthropic 请求→Anthropic 上游,代理两侧都能流式,最通用。
> 若你的上游只有 Anthropic 协议,另设 `anthropic_base_url`(在 `deepsee config set upstream.anthropic_base_url <地址>`)。

### [3/3] 自动集成
探测到的都会问:`Claude Code Hook`(推荐 y)、`Codex skill`、`OpenCode 补丁`、`MCP`。

最后会打印**配置摘要**,确认 `y` 才写入文件(`n` 不修改任何文件)。

需要改端口或启用可选文生图时再运行 `deepsee setup --advanced`;高级向导为 5 步。默认流程只聚焦“图片→文字→DeepSeek”。

---

## 5. 装进 Claude Code(最常用)

```bash
deepsee install          # 装 Claude Code Hook + MCP(带备份)
# 重启 Claude Code,然后:
#   · 直接贴图/截图 → Hook 自动转文字,DeepSeek 读懂
#   · 或让模型调用 describe_image 工具
```

**Hook 的边界(务必了解)**:
- Hook 只在**交互式会话 + 模型主动 Read 图片路径**时生效。
- **headless(`claude -p`)与"粘贴图"(消息里的 image 块)不走 Read**,Claude Code 对 updatedInput 可能不生效——这两种场景请用下面两条:
- Hook 写在 `settings.local.json`(不是 settings.json),这样 **cc-switch 切供应商重写 settings.json 也不会把它抹掉**。

### 主方案:把 Claude Code 路由到 deepsee 代理(覆盖所有图片场景)

```bash
deepsee route on     # 把 ANTHROPIC_BASE_URL 指到 deepsee 代理(写进 settings.local.json,覆盖 cc-switch)
# 重启 Claude Code 生效。
# 之后所有图片(粘贴/headless/Read)都在代理处由 Converter 转成文字,再转发你的上游。
# 代理默认 8081 端口,需常驻(`deepsee start -d`)。回退:`deepsee route off`。
```

```
Claude Code ──► deepsee 代理(8081,图片块→文字)──► 你的上游(DeepSeek/中继/cc-switch)──► 模型回答
```

> 代理作为中转,对话能力依赖代理常驻 + 上游 key/额度可用。切供应商不受影响(deepsee 代理只管转图,上游照旧)。
> 依赖 cc-switch 的场合:cc-switch 会把 ANTHROPIC_BASE_URL 写进 settings.json,deepsee 用 settings.local.json 覆盖它且不被重写。

### 兜底:MCP 工具
- `analyze_image` / `ocr_image` / `compare_images` 走 `deepsee mcp`,只依赖视觉 key(如智谱),**不依赖上游对话额度**——即使对话通道没额度,识图也照常可用。
- `describe_image` 是兼容旧客户端的别名;`analyze_image` 可指定 `error/diagram/chart/ui/screenshot` 任务。
- 回退:`deepsee uninstall`(从备份恢复)。

## 6. 其他客户端

| 客户端 | 方式 |
|---|---|
| **Codex** | `deepsee install --skill`(或把 provider 的 `base_url` 指到 `http://127.0.0.1:8081/v1`) |
| **Cursor / OpenCode / Cherry Studio / ChatBox / Cline** | `deepsee start` 起代理,填 `base_url=http://127.0.0.1:8081/v1` + `api_key=proxy.master_key` + `model=<上游模型>`。面板「状态」页可一键复制 |
| **网页聊天界面** | `deepsee web` / `deepsee ui`,浏览器里贴图即聊 |

---

## 7. 常见组合(直接抄)

| 场景 | vision | `upstream.base_url` | `format` | `anthropic_base_url` |
|---|---|---|---|---|
| DeepSeek 官方 + 智谱(推荐) | zhipu(免费) | `https://api.deepseek.com/v1` | `openai` | (自动推断) |
| 任意 OpenAI 中转 + 智谱 | zhipu | `<中转>/v1` | `openai` | — |
| Anthropic 兼容中继 + 智谱 | zhipu | `<中继>/v1` | `auto` | `<中继>` |
| DeepSeek + 本地 Ollama 识图 | ollama(gemma3) | DeepSeek 地址 | `openai` | — |
| 本地 Ollama + 本地 OCR | local-ocr | `http://localhost:11434/v1` | `openai` | — |
| 纯本地(零 key) | local-ocr | (无) | — | — |

---

## 8. `~/.deepsee/config.json` 字段参考

```jsonc
{
  "vision": {
    "chain": [                     // 视觉降级链:失败自动切下一个
      { "provider": "zhipu",       // 供应商名(demo/local-ocr 免 key)
        "base_url": "...",         // OpenAI 兼容视觉端点
        "model": "glm-4v-flash",
        "api_key": "...",
        "max_tokens": 1024 }       // glm-4v-flash 上限 1024
    ],
    "mode": "describe",            // describe | ocr | qa
    "cache_ttl": 300,              // 相同图片+问题缓存秒数;0=关闭
    "cache_max_entries": 128,
    "failure_cooldown": 30         // 失败供应商暂时跳过秒数
  },
  "upstream": {
    "base_url": "https://api.deepseek.com/v1",
    "anthropic_base_url": "",      // 可选:Anthropic 协议基础地址
    "api_key": "...",
    "model": "deepseek-v4-flash",
    "format": "auto"               // openai | anthropic | auto
  },
  "proxy":  { "port": 8081, "master_key": "", "max_body_mb": 25 },
  "generation": { "provider": "...", "base_url": "...", "model": "...", "api_key": "" },
  "web":    { "port": 8090, "max_body_mb": 25 },
  "agents": { "claude_code": { "hook": true, "target_models": ["deepseek"] } }
}
```

> 用 `deepsee config set <点路径> <值>` 修改,如:
> `deepsee config set upstream.base_url https://api.deepseek.com/v1`
> `deepsee config set upstream.format auto`

### 环境变量(容器/CI 推荐)

环境变量只在运行时覆盖配置,调用 `save()` 也不会把覆盖值写回磁盘:

| 环境变量 | 作用 |
|---|---|
| `DEEPSEE_VISION_BASE_URL` / `DEEPSEE_VISION_MODEL` / `DEEPSEE_VISION_API_KEY` | 第一视觉端点;配置文件没有视觉链时可直接创建运行时链 |
| `DEEPSEE_UPSTREAM_BASE_URL` / `DEEPSEE_UPSTREAM_MODEL` / `DEEPSEE_UPSTREAM_API_KEY` | DeepSeek/上游端点 |
| `DEEPSEE_UPSTREAM_ANTHROPIC_BASE_URL` / `DEEPSEE_UPSTREAM_FORMAT` | Anthropic 地址与协议选择 |
| `DEEPSEE_PROXY_MASTER_KEY` | 本地代理鉴权 key |

### 图片输入兼容

- OpenAI Chat:`image_url` 既可为 `{ "url": "..." }` 也可直接为字符串。
- OpenAI Responses 内容块:`input_image` + `image_url`。
- Anthropic:`source.type=base64` 或 `source.type=url`。
- CLI/MCP:本地路径、HTTP(S) URL、data URI;`deepsee compare` 支持 2–4 张图。
- 代理/Web 默认拒绝超过 25 MB 的请求体,可用 `max_body_mb` 调整。
- 两个服务默认只监听 `127.0.0.1`。不要把 Web 配置面板直接暴露公网;代理若使用非本机监听地址,务必设置 `DEEPSEE_PROXY_MASTER_KEY`。

---

## 9. 排障 FAQ

| 现象 | 原因 | 解决 |
|---|---|---|
| `401 Invalid API key` | 视觉或上游 key 无效/过期 | 到对应平台重新生成,换 key |
| `403 error code: 1010` | 上游中继过 Cloudflare,脚本 UA 被拦 | deepsee 已内置浏览器 UA;若仍拦,该中继可能限制脚本访问,换个中继 |
| `400 max_tokens 非法` | 该视觉模型 token 上限低(glm-4v-flash ≤1024) | 向导已按预设处理;手动配置时把该链条目 `max_tokens` 调到上限内 |
| 对话返回空 | 思考型模型 content 为空 | deepsee 已兼容 `reasoning_content`,升级到最新版 |
| 端口被占用 | 8081/8090 冲突 | `deepsee start -p 8082` / `deepsee web -p 8091` |
| 想撤销安装 | — | `deepsee uninstall`(从备份恢复) |

---

## 10. 零 API key 也能先跑

`deepsee setup` 选「演示模式」或「本地 OCR」即可零成本体验完整流程(演示模式不调任何 API)。

---

© 2026 deepsee · MIT License
