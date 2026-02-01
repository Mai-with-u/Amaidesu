"""
Bilibili 弹幕插件测试
"""

import pytest
import asyncio
from tests.test_plugin_utils import PluginTestBase, MockEventBus
from src.plugins.bili_danmaku.plugin import BiliDanmakuPlugin
from src.plugins.bili_danmaku.providers.bili_danmaku_provider import BiliDanmakuInputProvider


class TestBiliDanmakuPlugin(PluginTestBase):
    """测试 Bilibili 弹幕插件"""

    @pytest.mark.asyncio
    async def test_plugin_setup(self, plugin_factory):
        """测试插件设置"""
        # 准备配置
        config = {
            "name": "bili_danmaku",
            "version": "1.0.0",
            "enabled": True,
            "room_id": 12345678,
            "poll_interval": 3,
            "message_config": {
                "user_id": "test_user",
                "user_nickname": "测试用户",
                "user_cardname": "",
                "enable_group_info": False,
                "content_format": ["text"],
                "accept_format": ["text"],
            },
        }

        # 创建插件
        plugin = plugin_factory(BiliDanmakuPlugin, config)

        # 测试get_info
        info = plugin.get_info()
        assert info["name"] == "BiliDanmaku"
        assert info["version"] == "1.0.0"
        assert info["category"] == "input"

        # 设置插件
        event_bus = MockEventBus()
        providers = await plugin.setup(event_bus, config)

        # 验证Provider列表
        assert len(providers) == 1
        assert isinstance(providers[0], BiliDanmakuInputProvider)

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_factory):
        """测试插件禁用状态"""
        config = {
            "name": "bili_danmaku",
            "version": "1.0.0",
            "enabled": False,
            "room_id": 12345678,
        }

        plugin = plugin_factory(BiliDanmakuPlugin, config)
        event_bus = MockEventBus()

        providers = await plugin.setup(event_bus, config)

        # 禁用状态下不应创建Provider
        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_invalid_room_id(self, plugin_factory):
        """测试无效room_id"""
        config = {
            "name": "bili_danmaku",
            "version": "1.0.0",
            "enabled": True,
            "room_id": -1,  # 无效的room_id
        }

        plugin = plugin_factory(BiliDanmakuPlugin, config)
        event_bus = MockEventBus()

        providers = await plugin.setup(event_bus, config)

        # 无效room_id不应创建Provider
        assert len(providers) == 0

    @pytest.mark.asyncio
    async def test_plugin_cleanup(self, plugin_factory):
        """测试插件清理"""
        config = {
            "name": "bili_danmaku",
            "version": "1.0.0",
            "enabled": True,
            "room_id": 12345678,
            "poll_interval": 3,
        }

        plugin = plugin_factory(BiliDanmakuPlugin, config)
        event_bus = MockEventBus()

        await plugin.setup(event_bus, config)

        # 清理插件
        await plugin.cleanup()

        # 验证Provider列表已清空
        assert len(plugin._providers) == 0


class TestBiliDanmakuInputProvider:
    """测试 Bilibili 弹幕 InputProvider"""

    @pytest.mark.asyncio
    async def test_provider_lifecycle(self):
        """测试Provider生命周期"""
        config = {
            "room_id": 12345678,
            "poll_interval": 1,
            "message_config": {
                "user_id": "test_user",
                "user_nickname": "测试用户",
            },
        }

        provider = BiliDanmakuInputProvider(config)

        # 测试初始状态
        assert not provider.is_running

        # 启动Provider (测试启动和立即停止)
        start_task = asyncio.create_task(provider.start())

        # 等待一小段时间让Provider启动
        await asyncio.sleep(0.1)

        # 停止Provider
        await provider.stop()

        # 取消启动任务
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

        # 验证Provider已停止
        assert not provider.is_running

    @pytest.mark.asyncio
    async def test_invalid_config(self):
        """测试无效配置"""
        with pytest.raises(ValueError):
            config = {"room_id": -1}
            BiliDanmakuInputProvider(config)
