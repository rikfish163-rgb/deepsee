"""配置管理:~/deepsee/config.json 读写、供应商预设、深层合并、校验。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from .util import config_home, ensure_config_dir

CONFIG_FILE = "config.json"

# ---------------------------------------------------------------------------
# 供应商预设(OpenAI 兼容 /images 之外均为 chat 视觉端点)
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict[str, str]] = {
    "zhipu": {
        "label": "智谱 GLM-4V-Flash(免费)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-flash",
        "max_tokens": 1024,  # glm-4v-flash 上限 1024
        "fee": "免费",
        "note": "推荐默认;大陆直连",
    },
    "dashscope": {
        "label": "阿里百炼 qwen-vl",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
        "fee": "新用户免费额度",
        "note": "qwen3-vl-flash 更便宜",
    },
    "siliconflow": {
        "label": "硅基流动 Qwen2.5-VL",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-VL-72B-Instruct",
        "fee": "新用户免费额度",
        "note": "国内可直连",
    },
    "openai": {
        "label": "OpenAI GPT-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "fee": "按量付费",
        "note": "需代理环境",
    },
    "gemini": {
        "label": "Google Gemini Flash(OpenAI 兼容)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "fee": "有免费档",
        "note": "需代理环境",
    },
    "groq": {
        "label": "Groq 视觉模型",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.2-90b-vision-preview",
        "fee": "有免费档",
        "note": "",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen-2.5-vl-72b-instruct:free",
        "fee": "免费/付费混合",
        "note": "`:free` 模型免费",
    },
    "stepfun": {
        "label": "阶跃星辰 Step-1oV",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-1o-vision",
        "fee": "按量付费",
        "note": "",
    },
    "moonshot": {
        "label": "月之暗面 Kimi Vision",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k-vision-preview",
        "fee": "按量付费",
        "note": "",
    },
    "hunyuan": {
        "label": "腾讯混元 Vision",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "model": "hunyuan-vision",
        "fee": "按量付费",
        "note": "",
    },
    "lingyi": {
        "label": "零一万物 Yi-Vision",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-vision",
        "fee": "按量付费",
        "note": "",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "model": "",
        "fee": "",
        "note": "填 base_url + api_key + model",
    },
    # 免 API key 的本地/演示 provider
    "demo": {
        "label": "演示模式(无需 API key,先体验流程)",
        "base_url": "",
        "model": "",
        "fee": "免费",
        "note": "不调真实 API,返回固定文案",
    },
    "local-ocr": {
        "label": "本地 OCR(离线免 key,提取图片文字)",
        "base_url": "",
        "model": "",
        "fee": "免费离线",
        "note": "需安装 deepsee[ocr]",
    },
    "ollama": {
        "label": "Ollama gemma3(本地免 key)",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "gemma3",
        "fee": "免费离线",
        "note": "先运行 `ollama pull gemma3`;兼容 OpenAI 视觉接口",
    },
}

# 不需要 api_key / base_url 的 provider(本地或演示)
NO_KEY_PROVIDERS = {"demo", "local-ocr", "ollama"}
NO_ENDPOINT_PROVIDERS = {"demo", "local-ocr"}

# 文生图后端预设(OpenAI 兼容 /images/generations)
GEN_PROVIDERS: dict[str, dict[str, str]] = {
    "siliconflow": {
        "label": "硅基流动 FLUX.1-schnell(免费)",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "black-forest-labs/FLUX.1-schnell-free",
        "fee": "免费",
    },
    "ark": {
        "label": "火山方舟 doubao-seedream",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seedream-3-0-t2i-250415",
        "fee": "按量付费",
    },
    "dashscope": {
        "label": "阿里百炼 wanx 通义万相",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "model": "wanx2.1-t2i-turbo",
        "fee": "按量付费",
    },
    "openai": {
        "label": "OpenAI gpt-image-1",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-image-1",
        "fee": "按量付费",
    },
    "openrouter": {
        "label": "OpenRouter 免费文生图",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "black-forest-labs/flux-schnell:free",
        "fee": "免费档",
    },
    "custom": {
        "label": "自定义文生图端点",
        "base_url": "",
        "model": "",
        "fee": "",
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "vision": {
        "chain": [],
        "mode": "describe",
        "max_tokens": 2048,
        "timeout": 60,
        "max_px": 1024,
        "cache_ttl": 300,
        "cache_max_entries": 128,
        "failure_cooldown": 30,
    },
    "upstream": {
        "base_url": "https://api.deepseek.com/v1",
        "anthropic_base_url": "",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "format": "auto",  # openai | anthropic | auto(OpenAI进→OpenAI上游,Anthropic进→Anthropic上游)
    },
    "proxy": {
        "port": 8081,  # 8080 常被本机其他服务占用,默认换 8081
        "master_key": "",
        "max_body_mb": 25,
    },
    "generation": {
        "provider": "siliconflow",
        "api_key": "",
        "model": "",
        "size": "1024x1024",
    },
    "web": {
        "port": 8090,
        "max_body_mb": 25,
    },
    "agents": {
        "claude_code": {
            "hook": False,
            # 需要桥接的目标模型子串列表;空列表 = 对全部模型桥接(默认)。
            # 装 Hook 的本意就是给纯文本模型用(DeepSeek 等)。若你实际用的
            # 模型原生支持看图且不想走转换,设一个永不匹配的值即可禁用,
            # 例如 target_models=["__none__"];详见 docs/CONFIGURATION.md。
            "target_models": [],
        },
        "codex": {"skill": False},
        "opencode": {"patched": False},
    },
}

# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

_load_cache: dict = {"path": None, "mtime_ns": None, "data": None}


def config_path() -> str:
    return os.path.join(ensure_config_dir(), CONFIG_FILE)


def _merge_inplace(base: dict, override: dict) -> dict:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_inplace(base[k], v)
        else:
            base[k] = deepcopy(v)
    return base


def deep_merge(base: dict, override: dict) -> dict:
    return _merge_inplace(deepcopy(base), override)


def _apply_env_overrides(base: dict) -> dict:
    """叠加显式 ``DEEPSEE_*`` 环境变量,且不修改磁盘配置缓存。

    这让容器、CI 和临时 shell 可以只靠环境变量启动,同时避免把环境中的
    secret 在下一次普通 ``load()`` 时意外写进 config.json。
    """
    data = deepcopy(base)

    vision_values = {
        "api_key": os.environ.get("DEEPSEE_VISION_API_KEY"),
        "base_url": os.environ.get("DEEPSEE_VISION_BASE_URL"),
        "model": os.environ.get("DEEPSEE_VISION_MODEL"),
    }
    chain = data.setdefault("vision", {}).setdefault("chain", [])
    # base_url/model 足以表达一个自定义 OpenAI 兼容视觉端点;key 可为空
    # (例如本地 Ollama)。仅有孤立 key 时不凭空创建无效端点。
    if not chain and (vision_values["base_url"] is not None or vision_values["model"] is not None):
        chain.append({
            "provider": "env",
            "base_url": vision_values["base_url"] or "",
            "model": vision_values["model"] or "",
            "api_key": vision_values["api_key"] or "",
        })
    elif chain:
        for key, value in vision_values.items():
            if value is not None:
                chain[0][key] = value

    upstream = data.setdefault("upstream", {})
    upstream_env = {
        "api_key": "DEEPSEE_UPSTREAM_API_KEY",
        "base_url": "DEEPSEE_UPSTREAM_BASE_URL",
        "anthropic_base_url": "DEEPSEE_UPSTREAM_ANTHROPIC_BASE_URL",
        "model": "DEEPSEE_UPSTREAM_MODEL",
        "format": "DEEPSEE_UPSTREAM_FORMAT",
    }
    for key, env_name in upstream_env.items():
        if env_name in os.environ:
            upstream[key] = os.environ[env_name]

    if "DEEPSEE_PROXY_MASTER_KEY" in os.environ:
        data.setdefault("proxy", {})["master_key"] = os.environ["DEEPSEE_PROXY_MASTER_KEY"]
    return data


def load() -> dict:
    """读配置。磁盘层按 mtime 缓存,环境变量在每次调用时动态叠加。"""
    p = config_path()
    try:
        mtime = os.stat(p).st_mtime_ns if os.path.exists(p) else None
    except OSError:
        mtime = None
    c = _load_cache
    if c["path"] == p and c["mtime_ns"] == mtime:
        return _apply_env_overrides(c["data"])
    data = _merge_inplace(deepcopy(DEFAULT_CONFIG), _read_json(p))
    c.update(path=p, mtime_ns=mtime, data=data)
    return _apply_env_overrides(data)


def _read_json(p: str) -> dict:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _remove_env_overrides_before_save(cfg: dict, p: str) -> dict:
    """保存前还原环境覆盖字段,防止运行时注入值被顺带持久化。"""
    data = deepcopy(cfg)
    disk = _merge_inplace(deepcopy(DEFAULT_CONFIG), _read_json(p))

    vision_env = {
        "api_key": "DEEPSEE_VISION_API_KEY",
        "base_url": "DEEPSEE_VISION_BASE_URL",
        "model": "DEEPSEE_VISION_MODEL",
    }
    if any(name in os.environ for name in vision_env.values()):
        out_chain = data.get("vision", {}).get("chain", [])
        disk_chain = disk.get("vision", {}).get("chain", [])
        if out_chain and not disk_chain and out_chain[0].get("provider") == "env":
            data["vision"]["chain"] = []
        elif out_chain:
            old = disk_chain[0] if disk_chain else {}
            for key, env_name in vision_env.items():
                if env_name in os.environ:
                    out_chain[0][key] = old.get(key, "")

    upstream_env = {
        "api_key": "DEEPSEE_UPSTREAM_API_KEY",
        "base_url": "DEEPSEE_UPSTREAM_BASE_URL",
        "anthropic_base_url": "DEEPSEE_UPSTREAM_ANTHROPIC_BASE_URL",
        "model": "DEEPSEE_UPSTREAM_MODEL",
        "format": "DEEPSEE_UPSTREAM_FORMAT",
    }
    for key, env_name in upstream_env.items():
        if env_name in os.environ:
            data.setdefault("upstream", {})[key] = disk.get("upstream", {}).get(
                key, DEFAULT_CONFIG["upstream"].get(key, "")
            )

    if "DEEPSEE_PROXY_MASTER_KEY" in os.environ:
        data.setdefault("proxy", {})["master_key"] = disk.get("proxy", {}).get(
            "master_key", ""
        )
    return data


def save(cfg: dict) -> str:
    p = config_path()
    tmp = p + ".tmp"
    persisted = _remove_env_overrides_before_save(cfg, p)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(persisted, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


# ---------------------------------------------------------------------------
# 校验 & 语义
# ---------------------------------------------------------------------------


def validate(cfg: dict) -> list[str]:
    """返回配置问题列表(空列表 = 正常)。"""
    problems: list[str] = []
    chain = cfg.get("vision", {}).get("chain", [])
    if not chain:
        problems.append("视觉链为空:运行 `deepsee setup` 或编辑 config.json 添加视觉 API")
    for i, item in enumerate(chain):
        provider = item.get("provider")
        base_url = item.get("base_url", "")
        if provider not in NO_KEY_PROVIDERS and not _is_local_endpoint(base_url) and not item.get("api_key"):
            problems.append(f"视觉链第 {i + 1} 项缺少 api_key")
        if provider not in NO_ENDPOINT_PROVIDERS and not base_url:
            problems.append(f"视觉链第 {i + 1} 项缺少 base_url")
    up = cfg.get("upstream", {})
    if not up.get("api_key"):
        problems.append("上游(DeepSeek)未配置 api_key(仅代理/会话路径需要,CLI 识图不需要)")
    return problems


def _is_local_endpoint(url: str) -> bool:
    """本机 OpenAI 兼容端点(如 Ollama/vLLM)通常无需 API key。"""
    try:
        import ipaddress
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if host == "localhost":
            return True
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def vision_chain(cfg: dict) -> list[dict]:
    return cfg.get("vision", {}).get("chain", []) or []
