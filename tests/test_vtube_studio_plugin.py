"""
VTube Studio Plugin 单元测试
"""

import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Mock pyvts before importing plugin
sys.modules["pyvts"] = MagicMock()

from src.plugins.vtube_studio.plugin import VTubeStudioPlugin  # noqa: E402
from src.plugins.vtube_studio.providers.vts_output_provider import VTSOutputProvider  # noqa: E402
from src.core.providers.base import RenderParameters  # noqa: E402


@pytest.fixture
def mock_config():
    """模拟配置"""
    return {
        "enabled": True,
        "plugin_name": "Test_VTS_Connector",
        "developer": "Test User",
        "authentication_token_path": "./test_vts_token.txt",
        "vts_host": "localhost",
        "vts_port": 8001,
        "lip_sync": {
            "enabled": False,  # 禁用口型同步以简化测试
        },
        "llm_matching_enabled": False,  # 禁用LLM以简化测试
    }


@pytest.fixture
def mock_event_bus():
    """模拟EventBus"""
    return MagicMock()


@pytest.mark.asyncio
async def test_plugin_initialization(mock_config):
    """测试插件初始化"""
    plugin = VTubeStudioPlugin(mock_config)

    assert plugin is not None
    assert plugin.config == mock_config
    assert plugin.enabled is True
    assert plugin.event_bus is None
    assert len(plugin._providers) == 0


@pytest.mark.asyncio
async def test_plugin_disabled(mock_config, mock_event_bus):
    """测试禁用的插件"""
    mock_config["enabled"] = False
    plugin = VTubeStudioPlugin(mock_config)

    assert plugin.enabled is False

    providers = await plugin.setup(mock_event_bus, mock_config)
    assert len(providers) == 0


@pytest.mark.asyncio
async def test_plugin_setup(mock_config, mock_event_bus):
    """测试插件设置"""
    plugin = VTubeStudioPlugin(mock_config)

    # 模拟VTS连接
    with patch.object(VTSOutputProvider, "setup", new_callable=AsyncMock) as mock_setup:
        mock_setup.return_value = None

        providers = await plugin.setup(mock_event_bus, mock_config)

        assert len(providers) == 1
        assert isinstance(providers[0], VTSOutputProvider)
        assert len(plugin._providers) == 1
        assert plugin.event_bus == mock_event_bus

        # 验证Provider的setup被调用
        mock_setup.assert_called_once()


@pytest.mark.asyncio
async def test_plugin_cleanup(mock_config, mock_event_bus):
    """测试插件清理"""
    plugin = VTubeStudioPlugin(mock_config)

    # 设置插件
    with patch.object(VTSOutputProvider, "setup", new_callable=AsyncMock):
        await plugin.setup(mock_event_bus, mock_config)

    # 清理插件
    with patch.object(VTSOutputProvider, "cleanup", new_callable=AsyncMock) as mock_cleanup:
        await plugin.cleanup()

        assert len(plugin._providers) == 0
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_plugin_get_info(mock_config):
    """测试获取插件信息"""
    plugin = VTubeStudioPlugin(mock_config)
    info = plugin.get_info()

    assert info["name"] == "VTubeStudio"
    assert info["version"] == "1.0.0"
    assert info["author"] == "Amaidesu Team"
    assert info["category"] == "output"
    assert info["api_version"] == "1.0"


@pytest.mark.asyncio
async def test_plugin_service_methods(mock_config):
    """测试服务接口方法"""
    plugin = VTubeStudioPlugin(mock_config)

    # 模拟Provider方法
    mock_provider = MagicMock()
    mock_provider.trigger_hotkey = AsyncMock(return_value=True)
    mock_provider.set_parameter_value = AsyncMock(return_value=True)
    mock_provider.close_eyes = AsyncMock(return_value=True)
    mock_provider.open_eyes = AsyncMock(return_value=True)
    mock_provider.smile = AsyncMock(return_value=True)

    plugin._providers = [mock_provider]

    # 测试触发热键
    result = await plugin.trigger_hotkey("test_hotkey")
    assert result is True
    mock_provider.trigger_hotkey.assert_called_once_with("test_hotkey")

    # 测试设置参数
    result = await plugin.set_parameter_value("TestParam", 0.5, 1.0)
    assert result is True
    mock_provider.set_parameter_value.assert_called_once_with("TestParam", 0.5, 1.0)

    # 测试闭眼
    result = await plugin.close_eyes()
    assert result is True
    mock_provider.close_eyes.assert_called_once()

    # 测试睁眼
    result = await plugin.open_eyes()
    assert result is True
    mock_provider.open_eyes.assert_called_once()

    # 测试微笑
    result = await plugin.smile(1.0)
    assert result is True
    mock_provider.smile.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_provider_render_expression(mock_config, mock_event_bus):
    """测试Provider渲染表情"""
    plugin = VTubeStudioPlugin(mock_config)

    with patch.object(VTSOutputProvider, "setup", new_callable=AsyncMock):
        await plugin.setup(mock_event_bus, mock_config)

    provider = plugin._providers[0]

    # 模拟VTS连接状态
    provider._is_connected_and_authenticated = True

    # 渲染微笑表情
    render_params = RenderParameters(
        content="1.0",
        render_type="expression",
        metadata={
            "expression_type": "smile",
        },
    )

    with patch.object(provider, "smile", new_callable=AsyncMock) as mock_smile:
        await provider.render(render_params)
        mock_smile.assert_called_once_with("1.0")


@pytest.mark.asyncio
async def test_provider_render_parameter(mock_config, mock_event_bus):
    """测试Provider渲染参数"""
    plugin = VTubeStudioPlugin(mock_config)

    with patch.object(VTSOutputProvider, "setup", new_callable=AsyncMock):
        await plugin.setup(mock_event_bus, mock_config)

    provider = plugin._providers[0]

    # 模拟VTS连接状态
    provider._is_connected_and_authenticated = True

    # 渲染参数设置
    render_params = RenderParameters(
        content="0.5",
        render_type="parameter",
        metadata={
            "param_name": "TestParam",
            "weight": 1.0,
        },
    )

    with patch.object(provider, "set_parameter_value", new_callable=AsyncMock) as mock_set_param:
        await provider.render(render_params)
        mock_set_param.assert_called_once_with("TestParam", 0.5, 1.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
