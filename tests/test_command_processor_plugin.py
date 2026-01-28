"""
Command Processor Plugin 测试

测试命令处理器插件的功能:
- 命令识别
- 命令执行
- 文本清理
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from src.plugins.command_processor.plugin import CommandProcessorPlugin
from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo


class TestCommandProcessorPlugin:
    """测试CommandProcessorPlugin"""

    @pytest.fixture
    def plugin_config(self):
        """插件配置"""
        return {
            "enabled": True,
            "command_pattern": r"%{([^}]+)}%",  # 正则表达式匹配 %{...}%
        }

    @pytest.fixture
    def event_bus(self):
        """Mock EventBus"""
        event_bus = Mock()
        event_bus.on = Mock()
        event_bus.off = Mock()
        event_bus.emit = AsyncMock()
        return event_bus

    @pytest.fixture
    def plugin(self, plugin_config, event_bus):
        """插件实例"""
        return CommandProcessorPlugin(plugin_config)

    @pytest.mark.asyncio
    async def test_setup(self, plugin, event_bus):
        """测试插件setup"""
        providers = await plugin.setup(event_bus, plugin.config)

        assert len(providers) == 0  # 无Provider
        event_bus.on.assert_called_once()
        call_args = event_bus.on.call_args
        assert call_args[0][0] == "message.received"

    @pytest.mark.asyncio
    async def test_cleanup(self, plugin, event_bus):
        """测试插件cleanup"""
        await plugin.setup(event_bus, plugin.config)
        await plugin.cleanup()

        event_bus.off.assert_called_once()

    def test_plugin_info(self, plugin):
        """测试插件信息"""
        info = plugin.get_info()

        assert info["name"] == "CommandProcessor"
        assert info["category"] == "processing"
        assert "version" in info

    @pytest.mark.asyncio
    async def test_handle_message_with_command(self, plugin, event_bus):
        """测试处理包含命令的消息"""
        # 准备测试消息
        user_info = UserInfo(platform="test", user_id="test_user", user_nickname="Test User", user_cardname="Test")
        format_info = FormatInfo(content_format=["text"], accept_format=["text"])
        message_info = BaseMessageInfo(
            platform="test",
            message_id="test_001",
            time=1234567890.0,
            user_info=user_info,
            group_info=None,
            template_info=None,
            format_info=format_info,
            additional_config={},
        )
        message = MessageBase(
            message_info=message_info,
            message_segment=Seg(type="text", data="Hello %{vts_trigger_hotkey}%!"),
            raw_message="",
        )

        # Setup插件
        await plugin.setup(event_bus, plugin.config)

        # 处理消息
        data = {"message": message}
        await plugin._handle_message("message.received", data, "test")

        # 验证事件发布
        assert event_bus.emit.call_count >= 1

        # 验证文本被清理
        assert message.message_segment.data == "Hello !"

    @pytest.mark.asyncio
    async def test_handle_message_without_command(self, plugin, event_bus):
        """测试处理不包含命令的消息"""
        # 准备测试消息
        user_info = UserInfo(platform="test", user_id="test_user", user_nickname="Test User", user_cardname="Test")
        format_info = FormatInfo(content_format=["text"], accept_format=["text"])
        message_info = BaseMessageInfo(
            platform="test",
            message_id="test_002",
            time=1234567890.0,
            user_info=user_info,
            group_info=None,
            template_info=None,
            format_info=format_info,
            additional_config={},
        )
        message = MessageBase(
            message_info=message_info, message_segment=Seg(type="text", data="Hello world!"), raw_message=""
        )

        # Setup插件
        await plugin.setup(event_bus, plugin.config)

        # 处理消息
        data = {"message": message}
        await plugin._handle_message("message.received", data, "test")

        # 验证事件未发布
        event_bus.emit.assert_not_called()

        # 验证文本未改变
        assert message.message_segment.data == "Hello world!"

    @pytest.mark.asyncio
    async def test_plugin_disabled(self, plugin_config, event_bus):
        """测试禁用状态的插件"""
        plugin_config["enabled"] = False
        plugin = CommandProcessorPlugin(plugin_config)

        providers = await plugin.setup(event_bus, plugin_config)

        assert len(providers) == 0
        event_bus.on.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_multiple_commands(self, plugin, event_bus):
        """测试处理包含多个命令的消息"""
        # 准备测试消息
        user_info = UserInfo(platform="test", user_id="test_user", user_nickname="Test User", user_cardname="Test")
        format_info = FormatInfo(content_format=["text"], accept_format=["text"])
        message_info = BaseMessageInfo(
            platform="test",
            message_id="test_003",
            time=1234567890.0,
            user_info=user_info,
            group_info=None,
            template_info=None,
            format_info=format_info,
            additional_config={},
        )
        message = MessageBase(
            message_info=message_info,
            message_segment=Seg(type="text", data="%{vts_trigger_hotkey}%Hello%{vts_trigger_hotkey}%world!"),
            raw_message="",
        )

        # Setup插件
        await plugin.setup(event_bus, plugin.config)

        # 处理消息
        data = {"message": message}
        await plugin._handle_message("message.received", data, "test")

        # 验证文本被清理
        assert message.message_segment.data == "Helloworld!"
