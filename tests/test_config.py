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
