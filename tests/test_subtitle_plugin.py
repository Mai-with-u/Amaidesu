"""
Subtitle Plugin 测试

测试SubtitlePlugin和SubtitleOutputProvider的功能。
"""

import pytest

from src.plugins.subtitle.subtitle_output_provider import SubtitleOutputProvider
from src.plugins.subtitle.plugin import SubtitlePlugin
from src.core.providers.base import RenderParameters
from tests.test_plugin_utils import MockEventBus


class TestOutlineLabel:
    """测试OutlineLabel（需要GUI环境，这里只测试逻辑）"""

    def test_label_init_without_ctk(self):
        """测试没有CTK时的初始化"""
        # 由于OutlineLabel需要CTK，这里只测试初始化逻辑
        # 实际GUI测试需要在有GUI环境的机器上运行
        pass


class TestSubtitleOutputProvider:
    """测试SubtitleOutputProvider"""

    @pytest.fixture
    def provider_config(self):
        """提供Provider配置"""
        return {
            "window_width": 800,
            "window_height": 100,
            "font_family": "Microsoft YaHei UI",
            "font_size": 28,
            "text_color": "white",
            "outline_enabled": True,
            "outline_color": "black",
            "outline_width": 2,
            "background_color": "white",
            "window_alpha": 0.95,
            "always_on_top": False,
            "always_show_window": False,  # 测试时不显示窗口
            "auto_hide": True,
            "fade_delay_seconds": 5,
        }

    @pytest.fixture
    def event_bus(self):
        """提供EventBus"""
        return MockEventBus()

    def test_provider_init(self, provider_config):
        """测试Provider初始化"""
        provider = SubtitleOutputProvider(provider_config)

        assert provider.window_width == 800
        assert provider.window_height == 100
        assert provider.font_family == "Microsoft YaHei UI"
        assert provider.font_size == 28
        assert provider.outline_enabled
        assert provider.auto_hide
        assert provider.fade_delay_seconds == 5

    @pytest.mark.asyncio
    async def test_provider_setup(self, provider_config, event_bus):
        """测试Provider设置"""
        provider = SubtitleOutputProvider(provider_config, event_bus)
        await provider.setup(event_bus, provider_config)

        assert provider.is_setup
        assert provider.event_bus == event_bus

    @pytest.mark.asyncio
    async def test_provider_render(self, provider_config, event_bus):
        """测试Provider渲染"""
        provider = SubtitleOutputProvider(provider_config, event_bus)
        await provider.setup(event_bus, provider_config)

        # 创建渲染参数
        params = RenderParameters(content="测试字幕", render_type="subtitle", priority=100)

        # 渲染（只测试不报错）
        await provider.render(params)

    @pytest.mark.asyncio
    async def test_provider_cleanup(self, provider_config, event_bus):
        """测试Provider清理"""
        provider = SubtitleOutputProvider(provider_config, event_bus)
        await provider.setup(event_bus, provider_config)

        # 清理
        await provider.cleanup()

        assert not provider.is_setup


class TestSubtitlePlugin:
    """测试SubtitlePlugin"""

    @pytest.fixture
    def plugin_config(self):
        """提供插件配置"""
        return {
            "enabled": True,
            "subtitle_display": {
                "window_width": 800,
                "window_height": 100,
                "font_size": 28,
                "text_color": "white",
                "always_show_window": False,  # 测试时不显示窗口
            },
        }

    @pytest.fixture
    def event_bus(self):
        """提供EventBus"""
        return MockEventBus()

    @pytest.mark.asyncio
    async def test_plugin_init(self, plugin_config):
        """测试Plugin初始化"""
        plugin = SubtitlePlugin(plugin_config)

        assert plugin.config == plugin_config
        assert plugin._providers == []

    @pytest.mark.asyncio
    async def test_plugin_setup(self, plugin_config, event_bus):
        """测试Plugin设置"""
        plugin = SubtitlePlugin(plugin_config)
        providers = await plugin.setup(event_bus, plugin_config)

        # 验证返回的Provider列表
        assert len(providers) == 1
        assert isinstance(providers[0], SubtitleOutputProvider)

        # 验证Provider已添加到插件
        assert len(plugin._providers) == 1

    @pytest.mark.asyncio
    async def test_plugin_cleanup(self, plugin_config, event_bus):
        """测试Plugin清理"""
        plugin = SubtitlePlugin(plugin_config)
        await plugin.setup(event_bus, plugin_config)

        # 清理
        await plugin.cleanup()

        # 验证Provider已清理
        assert len(plugin._providers) == 0

    @pytest.mark.asyncio
    async def test_plugin_info(self, plugin_config):
        """测试Plugin信息"""
        plugin = SubtitlePlugin(plugin_config)
        info = plugin.get_info()

        # 验证基本信息
        assert info["name"] == "Subtitle"
        assert info["version"] == "1.0.0"
        assert info["category"] == "output"
        assert "author" in info
        assert "description" in info
        assert "api_version" in info

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_config, event_bus):
        """测试插件被禁用的情况"""
        plugin_config["enabled"] = False
        plugin = SubtitlePlugin(plugin_config)
        providers = await plugin.setup(event_bus, plugin_config)

        # 验证没有返回Provider
        assert len(providers) == 0
        assert len(plugin._providers) == 0
