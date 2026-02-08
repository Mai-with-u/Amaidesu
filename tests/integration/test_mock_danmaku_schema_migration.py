"""测试 mock_danmaku Provider 自管理 Schema 集成"""

import pytest
from pathlib import Path
import tempfile

from src.domains.input.providers.mock_danmaku import MockDanmakuInputProvider
from src.core.provider_registry import ProviderRegistry
from src.services.config.schemas.schemas import get_provider_schema
from src.services.config.schemas.schemas.base import BaseProviderConfig
from pydantic import ValidationError


def test_mock_danmaku_has_config_schema():
    """测试 mock_danmaku Provider 有 ConfigSchema"""
    assert hasattr(MockDanmakuInputProvider, "ConfigSchema")
    assert issubclass(MockDanmakuInputProvider.ConfigSchema, BaseProviderConfig)


def test_mock_danmaku_schema_fields():
    """测试 ConfigSchema 包含所有必要字段"""
    schema = MockDanmakuInputProvider.ConfigSchema

    # 检查字段存在
    assert "type" in schema.model_fields
    assert "log_file_path" in schema.model_fields
    assert "send_interval" in schema.model_fields
    assert "loop_playback" in schema.model_fields
    assert "start_immediately" in schema.model_fields

    # 检查默认值
    default_instance = schema()
    assert default_instance.log_file_path == "msg_default.jsonl"
    assert default_instance.send_interval == 1.0
    assert default_instance.loop_playback is True
    assert default_instance.start_immediately is True


def test_mock_danmaku_provider_loads_with_valid_config():
    """测试 Provider 能正确加载有效配置"""
    config = {
        "log_file_path": "test.jsonl",
        "send_interval": 2.0,
        "loop_playback": False,
    }

    provider = MockDanmakuInputProvider(config)

    assert provider.typed_config.log_file_path == "test.jsonl"
    assert provider.typed_config.send_interval == 2.0
    assert provider.typed_config.loop_playback is False
    assert provider.typed_config.start_immediately is True  # 默认值


def test_mock_danmaku_provider_validation_send_interval_minimum():
    """测试 send_interval 最小值验证"""
    # 低于最小值应该失败
    with pytest.raises(ValidationError):
        MockDanmakuInputProvider.ConfigSchema(send_interval=0.05)

    # 等于最小值应该成功
    schema = MockDanmakuInputProvider.ConfigSchema(send_interval=0.1)
    assert schema.send_interval == 0.1


def test_get_provider_schema_returns_self_managed_schema():
    """测试 get_provider_schema() 返回自管理 Schema"""
    schema = get_provider_schema("mock_danmaku", "input")

    assert schema is MockDanmakuInputProvider.ConfigSchema
    assert schema.__name__ == "ConfigSchema"


def test_provider_registry_has_mock_danmaku_schema():
    """测试 ProviderRegistry 已自动注册 mock_danmaku Schema"""
    schema = ProviderRegistry.get_config_schema("mock_danmaku")

    assert schema is not None
    assert schema is MockDanmakuInputProvider.ConfigSchema


def test_fallback_for_non_migrated_providers():
    """测试未迁移的 Provider 通过 fallback 机制工作"""
    # console_input 是未迁移的 Provider，应该能正常获取 Schema
    schema = get_provider_schema("console_input", "input")
    assert schema is not None

    # 验证 Schema 字段
    assert "user_id" in schema.model_fields
    assert "user_nickname" in schema.model_fields


def test_typed_config_is_instance_of_config_schema():
    """测试 typed_config 是 ConfigSchema 的实例"""
    config = {"send_interval": 1.5}
    provider = MockDanmakuInputProvider(config)

    assert isinstance(provider.typed_config, MockDanmakuInputProvider.ConfigSchema)
    assert provider.typed_config.send_interval == 1.5


def test_config_schema_generates_valid_toml():
    """测试 ConfigSchema 能生成有效的 TOML 配置"""
    schema = MockDanmakuInputProvider.ConfigSchema

    with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
        temp_path = Path(f.name)

    try:
        # 生成 TOML
        schema.generate_toml(temp_path, "mock_danmaku")

        # 验证文件存在
        assert temp_path.exists()

        # 读取并验证内容
        content = temp_path.read_text(encoding='utf-8')
        assert "mock_danmaku" in content
        assert 'log_file_path = "msg_default.jsonl"' in content
        assert "send_interval = 1.0" in content

    finally:
        # 清理
        if temp_path.exists():
            temp_path.unlink()
