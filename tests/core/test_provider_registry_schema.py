"""测试 ProviderRegistry ConfigSchema 自动提取功能"""

import pytest
from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.registry import ProviderRegistry
from src.modules.types.base.input_provider import InputProvider


@pytest.fixture(autouse=True)
def clear_registry():
    """每个测试前清空注册表并在测试后恢复"""
    # 保存当前的注册状态
    saved_input = dict(ProviderRegistry._input_providers)
    saved_decision = dict(ProviderRegistry._decision_providers)
    saved_output = dict(ProviderRegistry._output_providers)
    saved_schemas = dict(ProviderRegistry._config_schemas)

    # 清空注册表
    ProviderRegistry.clear_all()
    yield

    # 恢复注册状态
    ProviderRegistry._input_providers.clear()
    ProviderRegistry._decision_providers.clear()
    ProviderRegistry._output_providers.clear()
    ProviderRegistry._config_schemas.clear()

    ProviderRegistry._input_providers.update(saved_input)
    ProviderRegistry._decision_providers.update(saved_decision)
    ProviderRegistry._output_providers.update(saved_output)
    ProviderRegistry._config_schemas.update(saved_schemas)


class MockTestProviderWithSchema(InputProvider):
    """测试用的 Provider（带 ConfigSchema）"""

    class ConfigSchema(BaseProviderConfig):
        type: str = "mock_test"
        test_field: str = Field(default="test_value", description="测试字段")

    def __init__(self, config: dict):
        super().__init__(config)


class MockTestProviderWithoutSchema(InputProvider):
    """测试用的 Provider（无 ConfigSchema）"""

    def __init__(self, config: dict):
        super().__init__(config)


def test_schema_auto_extraction_on_register():
    """测试注册 Provider 时自动提取 ConfigSchema"""
    # 注册带 Schema 的 Provider
    ProviderRegistry.register_input("mock_test_with_schema", MockTestProviderWithSchema)

    # 验证 Schema 被自动提取
    schema = ProviderRegistry.get_config_schema("mock_test_with_schema")
    assert schema is not None
    assert schema is MockTestProviderWithSchema.ConfigSchema
    assert schema.__name__ == "ConfigSchema"


def test_get_schema_returns_none_for_provider_without_schema():
    """测试没有 ConfigSchema 的 Provider 返回 None"""
    # 注册不带 Schema 的 Provider
    ProviderRegistry.register_input("mock_test_without_schema", MockTestProviderWithoutSchema)

    # 验证返回 None
    schema = ProviderRegistry.get_config_schema("mock_test_without_schema")
    assert schema is None


def test_get_schema_returns_none_for_nonexistent_provider():
    """测试不存在的 Provider 返回 None"""
    schema = ProviderRegistry.get_config_schema("nonexistent_provider")
    assert schema is None


def test_schema_validation_works():
    """测试 ConfigSchema 能正确验证配置"""
    ProviderRegistry.register_input("mock_test_with_schema", MockTestProviderWithSchema)

    schema = ProviderRegistry.get_config_schema("mock_test_with_schema")

    # 验证有效配置
    valid_config = {"type": "mock_test", "test_field": "custom_value"}
    config_obj = schema(**valid_config)
    assert config_obj.test_field == "custom_value"

    # 验证默认值
    default_config = {}
    config_obj = schema(**default_config)
    assert config_obj.test_field == "test_value"


def test_clear_all_clears_schemas():
    """测试 clear_all() 清空 Schema 注册表"""
    ProviderRegistry.register_input("mock_test_with_schema", MockTestProviderWithSchema)

    # 确认 Schema 已注册
    assert ProviderRegistry.get_config_schema("mock_test_with_schema") is not None

    # 清空
    ProviderRegistry.clear_all()

    # 确认已清空
    assert ProviderRegistry.get_config_schema("mock_test_with_schema") is None
