"""
测试 ConsoleInputProvider

直接测试内置的 ConsoleInputProvider，验证其数据采集功能。
"""

import asyncio
import sys
from src.layers.input.providers.console_input_provider import ConsoleInputProvider
from src.core.event_bus import EventBus
from src.layers.input.input_layer import InputLayer
from src.layers.input.input_provider_manager import InputProviderManager


async def main():
    """测试 ConsoleInputProvider"""
    # 创建事件总线
    event_bus = EventBus()

    # 创建 InputProviderManager
    provider_manager = InputProviderManager(event_bus)

    # 创建 ConsoleInputProvider
    config = {
        "user_id": "test_user",
        "user_nickname": "测试用户"
    }
    console_provider = ConsoleInputProvider(config)

    # 创建 InputLayer
    input_layer = InputLayer(event_bus, provider_manager)
    await input_layer.setup()

    # 监听 NormalizedMessage 事件
    @event_bus.on("normalization.message_ready", priority=50)
    async def on_message_ready(event_name: str, event_data: dict, source: str):
        """处理消息就绪事件"""
        message = event_data.get("message")
        if message:
            print(f"\n✅ 收到标准化消息:")
            print(f"   来源: {message.source}")
            print(f"   类型: {message.message_type}")
            print(f"   内容: {message.content}")
            print(f"   原始文本: {message.original_text}\n")

    # 启动 Provider
    print("🚀 启动 ConsoleInputProvider...")
    print("💡 提示: 输入 'exit()' 退出\n")

    await provider_manager.start_all_providers([console_provider])

    try:
        # 运行10秒或直到用户输入 exit()
        await asyncio.sleep(30)
    except KeyboardInterrupt:
        print("\n⚠️  收到中断信号")
    finally:
        print("🛑 停止 Provider...")
        await provider_manager.stop_all_providers()
        await input_layer.cleanup()
        print("✅ 测试完成")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 程序已退出")
        sys.exit(0)
