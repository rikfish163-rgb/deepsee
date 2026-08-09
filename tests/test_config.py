"""config 读写/合并/校验测试。"""

from deepsee import config


def test_deep_merge():
    base = {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2]}
    out = config.deep_merge(base, {"b": {"y": 9}, "d": 4})
    assert out == {"a": 1, "b": {"x": 1, "y": 9}, "c": [1, 2], "d": 4}
    # 不改原对象
    assert base["b"]["y"] == 2


def test_defaults_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path))
    cfg = config.load()
    assert cfg["vision"]["chain"] == []
    assert cfg["upstream"]["model"] == "deepseek-v4-flash"
    assert cfg["proxy"]["port"] == 8081
    cfg["vision"]["chain"] = [{"provider": "zhipu", "api_key": "sk-x", "model": "glm-4v-flash"}]
    path = config.save(cfg)
    assert path == str(tmp_path / "config.json")
    reloaded = config.load()
    assert reloaded["vision"]["chain"][0]["provider"] == "zhipu"
    # 保存后权限应收紧(POSIX;Windows 的 chmod 是 no-op,跳过)
    import os
    if os.name != "nt":
        assert (os.stat(path).st_mode & 0o777) in (0o600, 0o400)


def test_validate_problems():
    cfg = config.load()
    problems = config.validate(cfg)
    assert any("视觉链为空" in p for p in problems)
    cfg["vision"]["chain"] = [{"provider": "zhipu", "api_key": "", "base_url": "x", "model": "m"}]
    problems = config.validate(cfg)
    assert any("缺少 api_key" in p for p in problems)


def test_provider_presets():
    assert config.PROVIDERS["zhipu"]["model"] == "glm-4v-flash"
    assert config.PROVIDERS["zhipu"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert config.GEN_PROVIDERS["siliconflow"]["model"].startswith("black-forest-labs")


def test_validate_exempts_nokey_providers():
    """demo / 本地 OCR 不需要 api_key 与 base_url。"""
    cfg = config.load()
    cfg["vision"]["chain"] = [
        {"provider": "demo", "base_url": "", "api_key": "", "model": ""},
        {"provider": "local-ocr", "base_url": "", "api_key": "", "model": ""},
    ]
    problems = config.validate(cfg)
    assert not any(("视觉链" in p) and ("key" in p or "base_url" in p) for p in problems)


def test_environment_overrides_are_runtime_only(tmp_path, monkeypatch):
    """显式 DEEPSEE_* 环境变量应覆盖运行时配置,但不能把 secret 写回磁盘。"""
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path))
    cfg = config.load()
    cfg["vision"]["chain"] = [{
        "provider": "custom", "base_url": "https://old.example/v1",
        "api_key": "old-vk", "model": "old-vlm",
    }]
    cfg["upstream"]["api_key"] = "old-uk"
    config.save(cfg)

    monkeypatch.setenv("DEEPSEE_VISION_API_KEY", "env-vk")
    monkeypatch.setenv("DEEPSEE_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("DEEPSEE_VISION_MODEL", "env-vlm")
    monkeypatch.setenv("DEEPSEE_UPSTREAM_API_KEY", "env-uk")
    monkeypatch.setenv("DEEPSEE_UPSTREAM_MODEL", "env-deepseek")
    loaded = config.load()

    assert loaded["vision"]["chain"][0]["api_key"] == "env-vk"
    assert loaded["vision"]["chain"][0]["base_url"] == "https://vision.example/v1"
    assert loaded["vision"]["chain"][0]["model"] == "env-vlm"
    assert loaded["upstream"]["api_key"] == "env-uk"
    assert loaded["upstream"]["model"] == "env-deepseek"

    import json

    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk["vision"]["chain"][0]["api_key"] == "old-vk"
    assert on_disk["upstream"]["api_key"] == "old-uk"

    # 即使调用方把 load() 的结果保存回去,环境注入的 secret 也不能落盘。
    loaded["web"]["port"] = 9999
    config.save(loaded)
    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk["vision"]["chain"][0]["api_key"] == "old-vk"
    assert on_disk["upstream"]["api_key"] == "old-uk"
    assert on_disk["web"]["port"] == 9999


def test_environment_can_bootstrap_custom_vision_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEE_HOME", str(tmp_path))
    monkeypatch.setenv("DEEPSEE_VISION_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("DEEPSEE_VISION_MODEL", "gemma3")
    loaded = config.load()
    assert loaded["vision"]["chain"] == [{
        "provider": "env",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "gemma3",
        "api_key": "",
    }]
    assert not any("视觉链" in problem for problem in config.validate(loaded))


def test_ollama_preset_needs_no_api_key():
    cfg = config.load()
    cfg["vision"]["chain"] = [dict(config.PROVIDERS["ollama"], provider="ollama")]
    problems = config.validate(cfg)
    assert not any("视觉链" in p for p in problems)

    cfg["vision"]["chain"][0]["base_url"] = ""
    assert any("base_url" in p for p in config.validate(cfg))
