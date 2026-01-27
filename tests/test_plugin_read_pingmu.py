"""
测试 ReadPingmuPlugin (屏幕读评插件）
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from tests.test_plugin_utils import PluginTestBase, MockEventBus


class TestReadPingmuPlugin(PluginTestBase):
    """测试 ReadPingmuPlugin"""

    @pytest.mark.asyncio
    async def test_plugin_setup(self, plugin_factory):
        """测试插件初始化"""
        from src.plugins.read_pingmu.plugin import ReadPingmuPlugin

        config = {
            "enabled": False,  # 禁用以避免启动真实组件
            "api_key": "test_key",
            "base_url": "https://test.com",
            "model_name": "test-model",
        }

        plugin = ReadPingmuPlugin(config)
        plugin._test_event_bus = MockEventBus()

        # 调用 setup
        providers = await plugin.setup(plugin._test_event_bus, config)

        # 验证
        assert isinstance(providers, list)
        assert len(providers) == 0  # 此插件不返回Provider

    @pytest.mark.asyncio
    async def test_get_plugin_info(self, plugin_factory):
        """测试获取插件信息"""
        from src.plugins.read_pingmu.plugin import ReadPingmuPlugin

        config = {"enabled": False}
        plugin = ReadPingmuPlugin(config)

        info = plugin.get_info()

        # 验证信息
        assert info["name"] == "ReadPingmu"
        assert info["version"] == "2.0.0"
        assert info["category"] == "input"
        assert "author" in info
        assert "description" in info

    @pytest.mark.asyncio
    async def test_plugin_cleanup(self, plugin_factory):
        """测试插件清理"""
        from src.plugins.read_pingmu.plugin import ReadPingmuPlugin

        config = {"enabled": False}
        plugin = ReadPingmuPlugin(config)
        plugin._test_event_bus = MockEventBus()

        await plugin.setup(plugin._test_event_bus, config)
        await plugin.cleanup()

        # 验证清理完成
        assert not plugin._running


class TestReadPingmuEvents:
    """测试 ReadPingmu 事件处理"""

    @pytest.mark.asyncio
    async def test_on_screen_change(self):
        """测试屏幕变化事件处理"""
        from src.plugins.read_pingmu.plugin import ReadPingmuPlugin

        config = {"enabled": False}
        plugin = ReadPingmuPlugin(config)
        plugin._test_event_bus = MockEventBus()

        await plugin.setup(plugin._test_event_bus, config)

        # 模拟屏幕变化数据
        change_data = {
            "difference_score": 50.0,
            "timestamp": 1234567890,
        }

        # Mock screen_reader
        plugin.screen_reader = Mock()
        plugin.screen_reader.process_screen_change = AsyncMock(return_value=None)

        # 调用处理函数
        await plugin._on_screen_change(change_data)

        # 验证
        plugin.screen_reader.process_screen_change.assert_called_once_with(change_data)

    @pytest.mark.asyncio
    async def test_on_context_update(self):
        """测试上下文更新事件处理"""
        from src.plugins.read_pingmu.plugin import ReadPingmuPlugin

        config = {"enabled": False}
        plugin = ReadPingmuPlugin(config)
        mock_event_bus = MockEventBus()
        plugin._test_event_bus = mock_event_bus

        await plugin.setup(mock_event_bus, config)

        # 模拟上下文更新数据
        analysis_result = Mock()
        analysis_result.new_current_context = "测试屏幕描述"

        data = {
            "analysis_result": analysis_result,
            "images_processed": 1,
            "statistics": {},
        }

        # 调用处理函数
        await plugin._on_context_update(data)

        # 验证事件被发送
        assert mock_event_bus.verify_emit_called(
            "screen_monitor.update",
            data={
                "description": "测试屏幕描述",
                "images_processed": 1,
                "statistics": {},
            },
        )
