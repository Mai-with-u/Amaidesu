"""多文件配置加载器与生成器（六文件布局）：生成内容、加载合并视图、meta 隔离、版本独立读取、漂移写回、采集器段回填"""

from types import SimpleNamespace
from typing import Optional

import pytest
import tomlkit

import src.modules.config.registry as registry_module
from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.config.multi_file_loader import (
    CONFIG_FILE_NAMES,
    _FILE_VALIDATION_SPECS,
    _apply_file_validation,
    generate_default_configs,
    get_config_version,
    load_config_dir,
    needs_generation,
)
from src.modules.config.schemas.base import BaseConfig, DriftReport


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "config"


def _text(config_dir, name: str) -> str:
    return (config_dir / name).read_text(encoding="utf-8-sig")


def test_generation_creates_every_file_with_expected_sections_and_baseline_version(config_dir) -> None:
    assert needs_generation(config_dir) is True
    generate_default_configs(config_dir)
    assert needs_generation(config_dir) is False
    assert sorted(f.name for f in config_dir.glob("*.toml")) == sorted(CONFIG_FILE_NAMES)
    assert all(f'version = "{CONFIG_BASELINE_VERSION}"' in _text(config_dir, name) for name in CONFIG_FILE_NAMES)

    agents = _text(config_dir, "agents.toml")
    for section in (
        "[meta]",
        "[agents]",
        "[agents.streamer]",
        "[agents.streamer.persona]",
        "[agents.streamer.context]",
    ):
        assert section in agents
    assert "[agents.streamer.background]" in agents

    model = _text(
        config_dir, "model.toml"
    )  # 三层结构：providers / models / profiles，dict 形态的 profiles 按 key 展开为子表
    assert "[[llm_providers]]" in model and "[[llm_models]]" in model
    for profile in ("planner", "replyer", "summary", "minecraft", "minecraft_builder", "vision", "simulator"):
        assert f"[llm_profiles.{profile}]" in model

    infra = _text(config_dir, "infra.toml")
    for section in (
        "[tts]",
        "[subtitle]",
        "[events]",
        "[interceptors.rate_limit]",
        "[dashboard]",
        "[logging]",
        "[simulator]",
    ):
        assert section in infra
    assert "[tools]" in _text(config_dir, "tools.toml")


def test_load_returns_every_scope_without_drift_and_keeps_values_and_hides_meta(config_dir) -> None:
    generate_default_configs(config_dir)
    config, report = load_config_dir(config_dir)
    assert not report.redundant and not report.missing
    assert all(scope in config for scope in ("agents", "collectors", "tools", "model", "storage", "infra"))
    persona = config["agents"]["agents"]["streamer"]["persona"]
    assert (persona["bot_name"], persona["audience_salutation"], config["infra"]["dashboard"]["port"]) == (
        "麦麦",
        "大家",
        60214,
    )
    assert all("meta" not in scope_data for scope_data in config.values())  # [meta] 是文件私有元数据，不进合并命名空间


def test_file_versions_are_read_independently(config_dir) -> None:
    generate_default_configs(config_dir)
    path = config_dir / "agents.toml"
    path.write_text(
        _text(config_dir, "agents.toml").replace(f'version = "{CONFIG_BASELINE_VERSION}"', 'version = "2.0.32"', 1),
        encoding="utf-8-sig",
    )
    assert get_config_version(config_dir, "agents.toml") == "2.0.32"
    assert get_config_version(config_dir, "infra.toml") == CONFIG_BASELINE_VERSION  # 改一个文件的版本不影响其他文件


def test_missing_key_drift_is_filled_from_defaults_on_load(config_dir) -> None:
    generate_default_configs(config_dir)
    path = config_dir / "agents.toml"
    doc = tomlkit.loads(_text(config_dir, "agents.toml"))
    del doc["agents"]["game_2048"]
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")
    config, report = load_config_dir(config_dir)
    assert not report.redundant and config["agents"]["agents"]["game_2048"]


def test_collector_section_backfill_strips_none_values(monkeypatch) -> None:
    """Schema 含 Optional-None 字段且值为 None 时，回填 dict 不含 None：TOML 无 null 字面量，裸 None 会在写回落盘时炸。

    注册表整表替换为仅含伪造段，隔离物化器（它会把真实采集器段全部补进 extras，污染断言面）。
    """

    class _FakeCollectorSchema(BaseConfig):
        name: str = "fake"
        maybe: Optional[int] = None

    monkeypatch.setattr(registry_module, "COLLECTOR_SCHEMAS", {"fake_collector": _FakeCollectorSchema})
    root = SimpleNamespace(enabled=[], __pydantic_extra__={"fake_collector": {"name": "x", "maybe": None}})
    report = DriftReport()
    _apply_file_validation(_FILE_VALIDATION_SPECS["collectors.toml"], root, {}, report)
    assert root.__pydantic_extra__["fake_collector"] == {"name": "x"} and not report.has_drift
