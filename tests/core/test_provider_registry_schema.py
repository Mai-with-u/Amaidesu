"""测试 ProviderRegistry ConfigSchema 自动提取功能"""

from src.core.provider_registry import ProviderRegistry
from src.core.base.input_provider import InputProvider
from src.services.config.schemas.schemas.base import BaseProviderConfig
from pydantic import Field


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
    # 清空注册表
    ProviderRegistry.clear_all()

    # 注册带 Schema 的 Provider
    ProviderRegistry.register_input("mock_test_with_schema", MockTestProviderWithSchema)

    # 验证 Schema 被自动提取
    schema = ProviderRegistry.get_config_schema("mock_test_with_schema")
    assert schema is not None
    assert schema is MockTestProviderWithSchema.ConfigSchema
    assert schema.__name__ == "ConfigSchema"


def test_get_schema_returns_none_for_provider_without_schema():
    """测试没有 ConfigSchema 的 Provider 返回 None"""
    ProviderRegistry.clear_all()

    # 注册不带 Schema 的 Provider
    ProviderRegistry.register_input("mock_test_without_schema", MockTestProviderWithoutSchema)

    # 验证返回 None
    schema = ProviderRegistry.get_config_schema("mock_test_without_schema")
    assert schema is None


def test_get_schema_returns_none_for_nonexistent_provider():
    """测试不存在的 Provider 返回 None"""
    ProviderRegistry.clear_all()

    schema = ProviderRegistry.get_config_schema("nonexistent_provider")
    assert schema is None


def test_schema_validation_works():
    """测试 ConfigSchema 能正确验证配置"""
    ProviderRegistry.clear_all()
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
    ProviderRegistry.clear_all()
    ProviderRegistry.register_input("mock_test_with_schema", MockTestProviderWithSchema)

    # 确认 Schema 已注册
    assert ProviderRegistry.get_config_schema("mock_test_with_schema") is not None

    # 清空
    ProviderRegistry.clear_all()

    # 确认已清空
    assert ProviderRegistry.get_config_schema("mock_test_with_schema") is None
