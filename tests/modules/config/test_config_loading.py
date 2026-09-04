"""
配置加载集成测试

测试二级配置合并系统：
1. Schema默认值（Pydantic）
2. 主配置覆盖（[agents.<agent>] / [tools.<pack>.config.<component>]）

兼容期保留旧 phase-based 查询 API（输入/输出/决策阶段）。
新查询应使用 tools/agents 段。

运行: uv run pytest tests/modules/config/test_config_loading.py -v
"""

import os
import tempfile

import pytest
from pydantic import BaseModel

from src.modules.config.service import ConfigService, deep_merge_configs


class InputProviderSchema(BaseModel):
    enabled: bool = True
    priority: int = 100
    message_prefix: str = ""
    max_messages: int = 10
    custom_field: str = "default_value"


class OutputProviderSchema(BaseModel):
    enabled: bool = True
    voice_id: str = "default"
    speed: float = 1.0
    volume: float = 0.8
    output_format: str = "mp3"


class DecisionProviderSchema(BaseModel):
    enabled: bool = True
    model: str = "default-model"
    temperature: float = 0.7
    max_tokens: int = 512
    timeout: int = 30


@pytest.fixture
def temp_base_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def _write_main_config(base_dir: str, content: str) -> None:
    """写入主配置文件（旧格式 config.toml，由 ConfigService 自动迁移）。"""
    config_path = os.path.join(base_dir, "config.toml")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)


def test_schema_defaults_only(temp_base_dir):
    """无主配置时使用 Schema 默认值。"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["enabled"] is True
    assert merged_config["priority"] == 100
    assert merged_config["message_prefix"] == ""
    assert merged_config["max_messages"] == 10
    assert merged_config["custom_field"] == "default_value"


def test_agent_enabled_check_based_on_list_v2(temp_base_dir):
    """agents 段 enabled 列表应包含用户启用项。"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[agents]
enabled = ["test_input", "another_input"]
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    agents_config = config_service.get_section("agents", {})
    enabled_list = agents_config.get("enabled", [])

    assert "test_input" in enabled_list
    assert len(enabled_list) == 2


def test_provider_not_initialized(temp_base_dir):
    config_service = ConfigService(base_dir=temp_base_dir)

    merged_config = config_service.get_config_with_defaults("test_input", "input")

    assert merged_config == {}


def test_invalid_provider_layer(temp_base_dir):
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_component", "invalid_phase")

    assert merged_config == {}


def test_empty_overrides_section(temp_base_dir):
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input"]
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 100


def test_deep_merge_basic_types():
    base = {"a": 1, "b": 2}
    override = {"b": 20, "c": 3}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["b"] == 20
    assert result["c"] == 3


def test_deep_merge_nested_dicts_simple():
    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"nested": {"y": 200, "z": 30}, "b": 2}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["nested"]["x"] == 10
    assert result["nested"]["y"] == 200
    assert result["nested"]["z"] == 30
    assert result["b"] == 2


def test_deep_merge_lists():
    base = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}

    result = deep_merge_configs(base, override)

    assert result["items"] == [4, 5]


def test_deep_merge_none_values():
    base = {"a": 1, "b": 2}
    override = {"b": None, "c": 3}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["b"] == 2
    assert result["c"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
