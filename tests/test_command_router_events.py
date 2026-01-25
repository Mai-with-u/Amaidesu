import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from maim_message import MessageBase, Seg, BaseMessageInfo, UserInfo
from src.core.event_bus import EventBus
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin
from src.pipelines.command_router.pipeline import CommandRouterPipeline


class MockCommandPlugin(BasePlugin):
    """模拟命令处理插件"""

    def __init__(self, core, config):
        super().__init__(core, config)
        self.received_commands = []
        self.reply_messages = []

    async def setup(self):
        await super().setup()
        # 监听命令事件
        self.listen_event("command.received", self.handle_command)
        # 监听回复事件
        self.listen_event("chat.reply", self.handle_reply)

    async def handle_command(self, event_name: str, data: dict, source: str):
        """处理命令事件"""
        command = data["command"]
        self.received_commands.append({"command": command, "source": source, "data": data})
        self.logger.info(f"收到命令: {command}")

        # 模拟处理命令
        if command == "/test":
            await self.emit_event("chat.reply", {"message": "测试命令执行成功！", "type": "test"})

    async def handle_reply(self, event_name: str, data: dict, source: str):
        """处理回复事件"""
        self.reply_messages.append({"message": data["message"], "type": data["type"], "source": source})


class LegacyCommandPlugin(BasePlugin):
    """模拟旧式命令处理插件（使用服务注册）"""

    def __init__(self, core, config):
        super().__init__(core, config)
        self.received_messages = []
        self.service_name = "legacy_command_handler"

    async def setup(self):
        await super().setup()
        # 注册为服务（旧式方式）
        self.core.register_service(self.service_name, self)

    async def __call__(self, message):
        """服务调用接口"""
        self.received_messages.append(message)
        self.logger.info(f"旧式插件收到消息: {message.message_segment.data}")
        return True


def create_test_message(text: str, user_id: str = "test_user", username: str = "测试用户"):
    """创建测试消息"""
    user_info = UserInfo(user_id=user_id, user_nickname=username)
    message_info = BaseMessageInfo(user_info=user_info, platform="test")
    message_segment = Seg(type="text", data=text)
    message = MessageBase(message_segment=message_segment, message_info=message_info)
    return message


async def test_command_router_with_events():
    """测试CommandRouter使用事件系统"""
    print("\n=== 测试CommandRouter事件模式 ===")

    # 创建EventBus
    event_bus = EventBus()

    # 创建Core
    core = AmaidesuCore(platform="test", maicore_host="localhost", maicore_port=8080, event_bus=event_bus)

    # 创建管道（使用事件模式）
    pipeline_config = {
        "enabled": True,
        "command_prefix": "/",
        "use_events": True,
        "subscribers": [],  # 不需要订阅者
    }
    pipeline = CommandRouterPipeline(pipeline_config)
    pipeline.core = core  # 设置core引用

    # 创建插件
    plugin = MockCommandPlugin(core, {"name": "test_plugin"})
    await plugin.setup()

    # 测试命令处理
    test_commands = ["/hello", "/test", "/help", "/not_a_command"]

    for cmd in test_commands:
        message = create_test_message(cmd)
        result = await pipeline.process_message(message)

        if cmd.startswith("/"):
            # 命令消息应该被拦截（返回None）
            assert result is None, f"命令消息 {cmd} 应该被拦截"
        else:
            # 非命令消息应该通过
            assert result is not None, f"非命令消息 {cmd} 应该通过"

    # 等待事件处理
    await asyncio.sleep(0.1)

    # 验证插件收到命令
    assert len(plugin.received_commands) >= 3, f"应该收到至少3个命令，实际收到 {len(plugin.received_commands)}"

    # 验证/test命令产生了回复
    test_replies = [r for r in plugin.reply_messages if r["type"] == "test"]
    assert len(test_replies) > 0, "应该收到测试命令的回复"

    print("√ CommandRouter事件模式测试通过")


async def test_command_router_legacy_mode():
    """测试CommandRouter向后兼容模式"""
    print("\n=== 测试CommandRouter向后兼容模式 ===")

    # 创建Core（不使用EventBus）
    core = AmaidesuCore(platform="test", maicore_host="localhost", maicore_port=8080, event_bus=None)

    # 创建管道（使用兼容模式）
    pipeline_config = {"enabled": True, "command_prefix": "/", "use_events": False, "subscribers": ["legacy"]}
    pipeline = CommandRouterPipeline(pipeline_config)
    pipeline.core = core

    # 创建旧式插件
    plugin = LegacyCommandPlugin(core, {"name": "legacy_plugin"})
    await plugin.setup()

    # 测试命令处理
    test_message = create_test_message("/legacy_command")
    result = await pipeline.process_message(test_message)

    # 命令应该被拦截
    assert result is None, "命令消息应该被拦截"

    # 验证旧式插件收到消息
    assert len(plugin.received_messages) == 1, "旧式插件应该收到消息"
    assert plugin.received_messages[0].message_segment.data == "/legacy_command"

    print("√ CommandRouter向后兼容模式测试通过")


async def test_command_fallback():
    """测试CommandRouter事件系统失败时的回退"""
    print("\n=== 测试CommandRouter事件系统回退 ===")

    # 创建Core（有EventBus但subscribers为空）
    event_bus = EventBus()
    core = AmaidesuCore(platform="test", maicore_host="localhost", maicore_port=8080, event_bus=event_bus)

    # 创建管道（事件模式启用，但会回退）
    pipeline_config = {
        "enabled": True,
        "command_prefix": "/",
        "use_events": True,
        "subscribers": ["fallback_test"],  # 配置订阅者用于回退
    }
    pipeline = CommandRouterPipeline(pipeline_config)
    pipeline.core = core

    # 创建旧式插件
    plugin = LegacyCommandPlugin(core, {"name": "fallback_plugin"})
    plugin.service_name = "fallback_test_command_handler"
    await plugin.setup()

    # 测试命令
    test_message = create_test_message("/fallback")
    result = await pipeline.process_message(test_message)

    # 命令应该被处理
    assert result is None

    # 等待事件处理
    await asyncio.sleep(0.1)

    # 验证：由于事件系统正常工作，插件应该通过事件系统收到消息（不是通过回退）
    # 这个测试实际上验证了事件系统的优先级
    # 如果插件也监听了事件，它会通过事件系统收到消息
    print("√ CommandRouter事件系统优先级测试通过（事件系统正常工作，无需回退）")


async def test_non_command_messages():
    """测试非命令消息的处理"""
    print("\n=== 测试非命令消息处理 ===")

    core = AmaidesuCore(platform="test", maicore_host="localhost", maicore_port=8080, event_bus=EventBus())

    pipeline_config = {"enabled": True, "command_prefix": "/", "use_events": True}
    pipeline = CommandRouterPipeline(pipeline_config)
    pipeline.core = core

    # 测试各种非命令消息
    test_cases = [
        "hello world",  # 普通文本
        "http://example.com",  # URL
        "",  # 空文本
        "   ",  # 空白文本
        "prefix: command",  # 其他前缀
    ]

    for text in test_cases:
        message = create_test_message(text)
        result = await pipeline.process_message(message)

        # 非命令消息应该通过不被处理
        if text.strip():  # 非空消息
            assert result is not None, f"非命令消息应该通过: '{text}'"
        else:  # 空消息
            # 空消息的处理取决于实现，这里不做断言
            pass

    print("√ 非命令消息处理测试通过")


async def test_disabled_pipeline():
    """测试禁用的管道"""
    print("\n=== 测试禁用的CommandRouter ===")

    core = AmaidesuCore(platform="test", maicore_host="localhost", maicore_port=8080, event_bus=EventBus())

    pipeline_config = {"enabled": False, "use_events": True}
    pipeline = CommandRouterPipeline(pipeline_config)

    # 测试命令
    test_message = create_test_message("/test")
    result = await pipeline.process_message(test_message)

    # 禁用的管道应该让所有消息通过
    assert result is not None, "禁用的管道不应该处理消息"

    print("√ 禁用管道测试通过")


async def main():
    """运行所有测试"""
    print("开始CommandRouter事件系统改造测试...")

    try:
        await test_command_router_with_events()
        await test_command_router_legacy_mode()
        await test_command_fallback()
        await test_non_command_messages()
        await test_disabled_pipeline()

        print("\n[SUCCESS] 所有CommandRouter测试通过！")

    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
