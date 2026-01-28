"""
Arknights Plugin 测试

测试明日方舟插件（空壳）的基本功能:
- 插件初始化
- setup/cleanup
- 插件信息
"""

import pytest
from unittest.mock import Mock

from src.plugins.arknights.plugin import ArknightsPlugin


class TestArknightsPlugin:
    """测试ArknightsPlugin（空壳）"""

    @pytest.fixture
    def plugin_config(self):
        """插件配置"""
        return {
            "enabled": True,
        }

    @pytest.fixture
    def event_bus(self):
        """Mock EventBus"""
        return Mock()

    @pytest.fixture
    def plugin(self, plugin_config, event_bus):
        """插件实例"""
        return ArknightsPlugin(plugin_config)

    @pytest.mark.asyncio
    async def test_setup(self, plugin, event_bus):
        """测试插件setup"""
        providers = await plugin.setup(event_bus, plugin.config)

        assert len(providers) == 0  # 空壳插件，无Provider

    @pytest.mark.asyncio
    async def test_cleanup(self, plugin, event_bus):
        """测试插件cleanup"""
        await plugin.setup(event_bus, plugin.config)
        await plugin.cleanup()
        # 只要不抛出异常就算成功

    def test_plugin_info(self, plugin):
        """测试插件信息"""
        info = plugin.get_info()

        assert info["name"] == "Arknights"
        assert info["category"] == "game"
        assert "version" in info

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_config, event_bus):
        """测试禁用状态的插件"""
        plugin_config["enabled"] = False
        plugin = ArknightsPlugin(plugin_config)

        providers = await plugin.setup(event_bus, plugin_config)

        assert len(providers) == 0
