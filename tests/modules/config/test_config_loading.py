"""
配置加载集成测试

测试二级配置合并系统：
1. Schema默认值（Pydantic）
2. 主配置覆盖（[collectors.{name}] / [deciders.{name}] / [handlers.{name}]）

注意: 本地配置（组件目录下的config.toml）功能已移除。

运行: uv run pytest tests/modules/config/test_config_loading.py -v
"""

import os
import tempfile

import pytest
from pydantic import BaseModel, ValidationError

from src.modules.config.service import ConfigService


# =============================================================================
# Test Fixtures - Pydantic Schemas
# =============================================================================


class InputProviderSchema(BaseModel):
    """测试输入 Collector Schema"""

    enabled: bool = True
    priority: int = 100
    message_prefix: str = ""
    max_messages: int = 10
    custom_field: str = "default_value"


class OutputProviderSchema(BaseModel):
    """测试输出 Handler Schema"""

    enabled: bool = True
    voice_id: str = "default"
    speed: float = 1.0
    volume: float = 0.8
    output_format: str = "mp3"


class DecisionProviderSchema(BaseModel):
    """测试决策 Decider Schema"""

    enabled: bool = True
    model: str = "default-model"
    temperature: float = 0.7
    max_tokens: int = 512
    timeout: int = 30


# =============================================================================
# Test Fixtures - Config Service
# =============================================================================


@pytest.fixture
def temp_base_dir():
    """创建临时测试目录"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def _write_main_config(base_dir: str, content: str) -> None:
    """写入主配置文件"""
    template_path = os.path.join(base_dir, "config-template.toml")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(content)


# =============================================================================
# Test 1: Schema默认值
# =============================================================================


def test_schema_defaults_only(temp_base_dir):
    """测试只有Schema默认值的情况"""
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

    assert merged_config["enabled"] is True
    assert merged_config["priority"] == 100
    assert merged_config["message_prefix"] == ""
    assert merged_config["max_messages"] == 10
    assert merged_config["custom_field"] == "default_value"


# =============================================================================
# Test 3: 配置优先级正确性
# =============================================================================


def test_config_priority_main_overrides_schema(temp_base_dir):
    """测试主配置覆盖优先级高于Schema默认值"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input"]

[collectors.test_input]
priority = 200
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 200


def test_config_priority_schema_defaults_lowest(temp_base_dir):
    """测试Schema默认值优先级最低"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[handlers]
enabled = ["test_output"]
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_output", "output", OutputProviderSchema)

    assert merged_config["voice_id"] == "default"
    assert merged_config["speed"] == 1.0
    assert merged_config["volume"] == 0.8
    assert merged_config["output_format"] == "mp3"
    assert merged_config["enabled"] is True


# =============================================================================
# Test 4: enabled字段不在组件配置中
# =============================================================================


def test_provider_config_without_enabled_field(temp_base_dir):
    """测试组件配置中没有enabled字段的情况（从Schema获取）"""
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

    assert merged_config["enabled"] is True
    assert merged_config["priority"] == 100


def test_provider_enabled_check_based_on_list(temp_base_dir):
    """测试enabled检查基于enabled列表"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input", "another_input"]
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    collectors_config = config_service.get_section("collectors", {})
    enabled_list = collectors_config.get("enabled", [])

    assert "test_input" in enabled_list
    assert len(enabled_list) == 2


# =============================================================================
# Test 5: 三种组件类型（input/output/decision）
# =============================================================================


def test_input_provider_config_loading(temp_base_dir):
    """测试输入 Collector 配置加载"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input"]

[collectors.test_input]
priority = 200
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 200
    assert merged_config["max_messages"] == 10
    assert merged_config["enabled"] is True


def test_output_provider_config_loading(temp_base_dir):
    """测试输出 Handler 配置加载"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[handlers]
enabled = ["test_output"]

[handlers.test_output]
volume = 0.6
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_output", "output", OutputProviderSchema)

    assert merged_config["voice_id"] == "default"
    assert merged_config["speed"] == 1.0
    assert merged_config["volume"] == 0.6
    assert merged_config["enabled"] is True


def test_decision_provider_config_loading(temp_base_dir):
    """测试决策 Decider 配置加载"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[deciders]
active = "test_decision"
available = ["test_decision"]

[deciders.test_decision]
max_tokens = 2048
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults(
        "test_decision", "decision", DecisionProviderSchema
    )

    assert merged_config["model"] == "default-model"
    assert merged_config["temperature"] == 0.7
    assert merged_config["max_tokens"] == 2048
    assert merged_config["enabled"] is True


# =============================================================================
# Test 6: 边界情况和错误处理
# =============================================================================


def test_provider_not_initialized(temp_base_dir):
    """测试未初始化时获取配置"""
    config_service = ConfigService(base_dir=temp_base_dir)

    merged_config = config_service.get_config_with_defaults("test_input", "input")

    assert merged_config == {}


def test_invalid_provider_layer(temp_base_dir):
    """测试无效的阶段"""
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
    """测试没有组件配置覆盖的情况"""
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


def test_deep_merge_nested_dicts(temp_base_dir):
    """测试深度合并嵌套字典"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input"]

[collectors.test_input]
priority = 200

[collectors.test_input.nested_config]
field2 = "override"
field3 = "new"
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    merged_config = config_service.get_config_with_defaults("test_input", "input", InputProviderSchema)

    assert merged_config["priority"] == 200
    assert "nested_config" in merged_config


# =============================================================================
# Test 7: load_global_overrides
# =============================================================================


def test_load_global_overrides(temp_base_dir):
    """测试加载主配置覆盖"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"

[collectors]
enabled = ["test_input"]

[collectors.test_input]
priority = 200
custom_field = "override_value"
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    overrides = config_service.load_global_overrides("input", "test_input")

    assert overrides["priority"] == 200
    assert overrides["custom_field"] == "override_value"


def test_load_global_overrides_not_exist(temp_base_dir):
    """测试加载不存在的主配置覆盖"""
    _write_main_config(
        temp_base_dir,
        """
[general]
platform_id = "test"
""",
    )

    config_service = ConfigService(base_dir=temp_base_dir)
    config_service.initialize()

    overrides = config_service.load_global_overrides("input", "nonexistent_component")

    assert overrides == {}


# =============================================================================
# Test 8: deep_merge_configs 函数
# =============================================================================


def test_deep_merge_basic_types():
    """测试深度合并基本类型"""
    from src.modules.config.service import deep_merge_configs

    base = {"a": 1, "b": 2}
    override = {"b": 20, "c": 3}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["b"] == 20
    assert result["c"] == 3


def test_deep_merge_nested_dicts_simple():
    """测试深度合并嵌套字典"""
    from src.modules.config.service import deep_merge_configs

    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"nested": {"y": 200, "z": 30}, "b": 2}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["nested"]["x"] == 10
    assert result["nested"]["y"] == 200
    assert result["nested"]["z"] == 30
    assert result["b"] == 2


def test_deep_merge_lists():
    """测试深度合并列表（后者替换前者）"""
    from src.modules.config.service import deep_merge_configs

    base = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}

    result = deep_merge_configs(base, override)

    assert result["items"] == [4, 5]


def test_deep_merge_none_values():
    """测试None值被跳过"""
    from src.modules.config.service import deep_merge_configs

    base = {"a": 1, "b": 2}
    override = {"b": None, "c": 3}

    result = deep_merge_configs(base, override)

    assert result["a"] == 1
    assert result["b"] == 2
    assert result["c"] == 3


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
