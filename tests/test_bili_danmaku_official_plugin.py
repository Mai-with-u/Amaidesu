"""
Bilibili 官方弹幕插件测试
"""

import pytest
from tests.test_plugin_utils import PluginTestBase, MockEventBus
from src.plugins.bili_danmaku_official.plugin import BiliDanmakuOfficialPlugin
from src.plugins.bili_danmaku_official.providers.bili_official_provider import BiliDanmakuOfficialInputProvider


class TestBiliDanmakuOfficialPlugin(PluginTestBase):
    """测试 Bilibili 官方弹幕插件"""

    @pytest.mark.asyncio
    async def test_plugin_setup(self, plugin_factory):
        """测试插件设置"""
        # 准备配置
        config = {
            "name": "bili_danmaku_official",
            "version": "1.0.0",
            "enabled": True,
            "id_code": "test_id_code",
            "app_id": 12345678,
            "access_key": "test_access_key",
            "access_key_secret": "test_secret",
            "api_host": "https://test.biliapi.com",
            "message_cache_size": 100,
            "context_tags": ["character", "setting"],
            "enable_template_info": False,
        }

        # 创建插件
        plugin = plugin_factory(BiliDanmakuOfficialPlugin, config)

        # 测试get_info
        info = plugin.get_info()
        assert info["name"] == "BiliDanmakuOfficial"
        assert info["version"] == "1.0.0"
        assert info["category"] == "input"

        # 设置插件
        event_bus = MockEventBus()
        providers = await plugin.setup(event_bus, config)

        # 验证Provider列表
        assert len(providers) == 1
        assert isinstance(providers[0], BiliDanmakuOfficialInputProvider)

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_factory):
        """测试插件禁用状态"""
        config = {
            "name": "bili_danmaku_official",
            "version": "1.0.0",
            "enabled": False,
            "id_code": "test_id_code",
            "app_id": 12345678,
            "access_key": "test_access_key",
            "access_key_secret": "test_secret",
        }

        plugin = plugin_factory(BiliDanmakuOfficialPlugin, config)
        event_bus = MockEventBus()

        providers = await plugin.setup(event_bus, config)

        # 禁用状态下不应创建Provider
        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_missing_config(self, plugin_factory):
        """测试缺失必需配置"""
        config = {
            "name": "bili_danmaku_official",
            "version": "1.0.0",
            "enabled": True,
            # 缺少必需配置
        }

        plugin = plugin_factory(BiliDanmakuOfficialPlugin, config)
        event_bus = MockEventBus()

        providers = await plugin.setup(event_bus, config)

        # 缺失配置不应创建Provider
        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_plugin_cleanup(self, plugin_factory):
        """测试插件清理"""
        config = {
            "name": "bili_danmaku_official",
            "version": "1.0.0",
            "enabled": True,
            "id_code": "test_id_code",
            "app_id": 12345678,
            "access_key": "test_access_key",
            "access_key_secret": "test_secret",
        }

        plugin = plugin_factory(BiliDanmakuOfficialPlugin, config)
        event_bus = MockEventBus()

        await plugin.setup(event_bus, config)

        # 清理插件
        await plugin.cleanup()

        # 验证Provider列表已清空
        assert len(plugin._providers) == 0


class TestBiliDanmakuOfficialInputProvider:
    """测试 Bilibili 官方弹幕 InputProvider"""

    @pytest.mark.asyncio
    async def test_provider_initialization(self):
        """测试Provider初始化"""
        config = {
            "id_code": "test_id_code",
            "app_id": 12345678,
            "access_key": "test_access_key",
            "access_key_secret": "test_secret",
            "message_cache_size": 100,
        }

        provider = BiliDanmakuOfficialInputProvider(config)

        # 测试初始状态
        assert not provider.is_running
        assert provider.message_cache_service is not None
        assert provider.websocket_client is not None

    @pytest.mark.asyncio
    async def test_invalid_config(self):
        """测试无效配置"""
        with pytest.raises(ValueError):
            config = {
                "id_code": "test",
                # 缺少其他必需配置
            }
            BiliDanmakuOfficialInputProvider(config)
