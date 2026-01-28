"""
Mainosaba Plugin 测试

测试Mainosaba插件的基本功能:
- 插件初始化
- setup/cleanup
- InputProvider创建
"""

import pytest
from unittest.mock import Mock

from src.plugins.mainosaba.plugin import MainosabaPlugin


class TestMainosabaPlugin:
    """测试MainosabaPlugin"""

    @pytest.fixture
    def plugin_config(self):
        """插件配置"""
        return {
            "enabled": True,
            "full_screen": True,
            "check_interval": 1,
            "response_timeout": 10,
            "control_method": "mouse_click",
            "click_position": [960, 540],
        }

    @pytest.fixture
    def event_bus(self):
        """Mock EventBus"""
        return Mock()

    @pytest.fixture
    def plugin(self, plugin_config, event_bus):
        """插件实例"""
        return MainosabaPlugin(plugin_config)

    @pytest.mark.asyncio
    async def test_setup(self, plugin, event_bus):
        """测试插件setup"""
        providers = await plugin.setup(event_bus, plugin.config)

        assert len(providers) == 1  # 有一个InputProvider
        assert providers[0].__class__.__name__ == "MainosabaInputProvider"

    @pytest.mark.asyncio
    async def test_cleanup(self, plugin, event_bus):
        """测试插件cleanup"""
        providers = await plugin.setup(event_bus, plugin.config)
        await plugin.cleanup()
        # 只要不抛出异常就算成功

    def test_plugin_info(self, plugin):
        """测试插件信息"""
        info = plugin.get_info()

        assert info["name"] == "Mainosaba"
        assert info["category"] == "input"
        assert "version" in info

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_config, event_bus):
        """测试禁用状态的插件"""
        plugin_config["enabled"] = False
        plugin = MainosabaPlugin(plugin_config)

        providers = await plugin.setup(event_bus, plugin_config)

        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_input_provider_config(self, plugin, event_bus):
        """测试InputProvider配置正确传递"""
        providers = await plugin.setup(event_bus, plugin.config)

        provider = providers[0]
        assert provider.config["check_interval"] == 1
        assert provider.config["response_timeout"] == 10
        assert provider.config["full_screen"] is True
