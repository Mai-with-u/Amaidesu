"""
测试Manager与ConfigService的集成
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from src.domains.input.input_provider_manager import InputProviderManager
from src.domains.output.manager import OutputProviderManager
from src.core.event_bus import EventBus


@pytest.fixture
def event_bus():
    """创建EventBus实例"""
    return EventBus()


@pytest.fixture
def config_service():
    """创建ConfigService实例（Mock）"""
    config_service = Mock()

    # Mock get_provider_config_with_defaults方法
    config_service.get_provider_config_with_defaults = Mock(
        return_value={
            "type": "console_input",
            "enabled": True,
        }
    )
    return config_service


@pytest.mark.asyncio
async def test_input_provider_manager_with_config_service(event_bus, config_service):
    """测试InputProviderManager使用ConfigService"""
    manager = InputProviderManager(event_bus)

    config = {
        "enabled": True,
        "enabled_inputs": ["console_input"],
    }

    # Mock ProviderRegistry.create_input
    with patch("src.core.provider_registry.ProviderRegistry") as mock_registry:
        mock_provider = Mock()
        mock_provider.__class__.__name__ = "ConsoleInputProvider"
        mock_registry.create_input.return_value = mock_provider

        # 测试新的配置加载方式（使用config_service）
        providers = await manager.load_from_config(config, config_service=config_service)

        # 验证结果
        assert len(providers) == 1
        assert mock_registry.create_input.called

        # 验证get_provider_config_with_defaults被调用
        assert config_service.get_provider_config_with_defaults.called
        call_args = config_service.get_provider_config_with_defaults.call_args
        assert call_args[1]["provider_name"] == "console_input"
        assert call_args[1]["provider_layer"] == "input"


@pytest.mark.asyncio
async def test_input_provider_manager_fallback(event_bus):
    """测试InputProviderManager向后兼容fallback（无config_service）"""
    manager = InputProviderManager(event_bus)

    config = {
        "enabled": True,
        "inputs": ["console"],  # 旧配置格式
        "inputs": {
            "console": {
                "type": "console",
                "enabled": True
            }
        }
    }

    # Mock ProviderRegistry.create_input
    with patch("src.core.provider_registry.ProviderRegistry") as mock_registry:
        mock_provider = Mock()
        mock_provider.__class__.__name__ = "ConsoleInputProvider"
        mock_registry.create_input.return_value = mock_provider

        # 测试fallback模式（不传入config_service）
        providers = await manager.load_from_config(config, config_service=None)

        # 验证结果
        assert len(providers) == 1
        assert mock_registry.create_input.called


@pytest.mark.asyncio
async def test_output_provider_manager_with_config_service(config_service):
    """测试OutputProviderManager使用ConfigService"""
    manager = OutputProviderManager()

    config = {
        "enabled": True,
        "enabled_outputs": ["subtitle"],
        "concurrent_rendering": True,
        "error_handling": "continue"
    }

    # Mock _create_provider方法
    async def mock_register(provider):
        manager.providers.append(provider)

    manager.register_provider = mock_register

    with patch.object(manager, "_create_provider") as mock_create:
        mock_provider = Mock()
        mock_create.return_value = mock_provider

        # 测试新的配置加载方式（使用config_service）
        await manager.load_from_config(config, config_service=config_service)

        # 验证结果
        assert len(manager.providers) == 1
        assert mock_create.called

        # 验证get_provider_config_with_defaults被调用
        assert config_service.get_provider_config_with_defaults.called
        call_args = config_service.get_provider_config_with_defaults.call_args
        assert call_args[1]["provider_name"] == "subtitle"
        assert call_args[1]["provider_layer"] == "output"


@pytest.mark.asyncio
async def test_output_provider_manager_fallback():
    """测试OutputProviderManager向后兼容fallback（无config_service）"""
    manager = OutputProviderManager()

    config = {
        "enabled": True,
        "enabled_outputs": ["subtitle"],
        "outputs": {
            "subtitle": {
                "type": "subtitle",
                "enabled": True
            }
        }
    }

    # Mock _create_provider方法
    async def mock_register(provider):
        manager.providers.append(provider)

    manager.register_provider = mock_register

    with patch.object(manager, "_create_provider") as mock_create:
        mock_provider = Mock()
        mock_create.return_value = mock_provider

        # 测试fallback模式（不传入config_service）
        await manager.load_from_config(config, config_service=None)

        # 验证结果
        assert len(manager.providers) == 1
        assert mock_create.called


@pytest.mark.asyncio
async def test_input_provider_supports_old_and_new_config_format(event_bus):
    """测试InputProviderManager支持新旧配置格式"""
    manager = InputProviderManager(event_bus)

    # Mock ProviderRegistry.create_input
    with patch("src.core.provider_registry.ProviderRegistry") as mock_registry:
        mock_provider = Mock()
        mock_provider.__class__.__name__ = "ConsoleInputProvider"
        mock_registry.create_input.return_value = mock_provider

        # 测试新格式（enabled_inputs）
        new_config = {
            "enabled": True,
            "enabled_inputs": ["console_input"]
        }
        providers_new = await manager.load_from_config(new_config, config_service=None)
        assert len(providers_new) == 1

        # 重置providers
        manager._providers = []

        # 测试旧格式（inputs）
        old_config = {
            "enabled": True,
            "inputs": ["console"],
            "inputs": {
                "console": {
                    "type": "console"
                }
            }
        }
        providers_old = await manager.load_from_config(old_config, config_service=None)
        assert len(providers_old) == 1


def test_enabled_inputs_field_takes_precedence(event_bus):
    """测试enabled_inputs字段优先于inputs字段"""
    manager = InputProviderManager(event_bus)

    config_with_both = {
        "enabled": True,
        "inputs": ["old_format"],
        "enabled_inputs": ["new_format"],
        "inputs": {
            "old_format": {"type": "old"},
            "new_format": {"type": "new"}
        }
    }

    # 读取配置时应该优先使用enabled_inputs
    enabled_inputs = config_with_both.get("enabled_inputs", config_with_both.get("inputs", []))
    assert enabled_inputs == ["new_format"]
