import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.event_bus import EventBus
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin


class TestPlugin1(BasePlugin):
    """测试插件1 - 发布事件"""

    async def setup(self):
        """设置插件"""
        self.counter = 0
        await super().setup()
        self.logger.info("TestPlugin1 设置完成")

    async def trigger_event(self):
        """触发测试事件"""
        self.counter += 1
        await self.emit_event("test.counter", {
            "count": self.counter,
            "message": f"这是第 {self.counter} 次触发"
        })


class TestPlugin2(BasePlugin):
    """测试插件2 - 监听事件"""

    async def setup(self):
        """设置插件"""
        await super().setup()
        self.received_events = []

        # 监听事件
        self.listen_event("test.counter", self.handle_counter_event)
        self.logger.info("TestPlugin2 设置完成，已监听 test.counter 事件")

    async def handle_counter_event(self, event_name: str, data: dict, source: str):
        """处理计数器事件"""
        self.logger.info(f"收到事件: {event_name}, 来源: {source}, 数据: {data}")
        self.received_events.append({
            "name": event_name,
            "data": data,
            "source": source
        })

        # 回应一个事件
        await self.emit_event("test.received", {
            "original_count": data["count"],
            "response": "已收到"
        })


class TestPlugin3(BasePlugin):
    """测试插件3 - 也监听事件"""

    async def setup(self):
        """设置插件"""
        await super().setup()
        self.logger.info("TestPlugin3 设置完成")

        # 监听回应事件
        self.listen_event("test.received", self.handle_response_event)

    async def handle_response_event(self, event_name: str, data: dict, source: str):
        """处理回应事件"""
        self.logger.info(f"收到回应: {event_name}, 来源: {source}, 数据: {data}")


async def test_event_bus_only():
    """仅测试EventBus功能"""
    print("\n=== 测试EventBus基础功能 ===")

    event_bus = EventBus()
    received = []

    def sync_handler(event_name, data, source):
        received.append(("sync", event_name, data, source))

    async def async_handler(event_name, data, source):
        await asyncio.sleep(0.1)  # 模拟异步操作
        received.append(("async", event_name, data, source))

    # 注册监听器
    event_bus.on("test.event", sync_handler)
    event_bus.on("test.event", async_handler)

    # 发布事件
    await event_bus.emit("test.event", {"message": "Hello World"}, "TestSource")

    # 等待异步处理完成
    await asyncio.sleep(0.2)

    # 验证结果
    assert len(received) == 2, f"应该收到2个事件，实际收到 {len(received)}"

    sync_event = next(e for e in received if e[0] == "sync")
    async_event = next(e for e in received if e[0] == "async")

    assert sync_event[1] == "test.event"
    assert async_event[1] == "test.event"
    assert sync_event[3] == "TestSource"

    print("√ EventBus基础功能测试通过")


async def test_core_with_eventbus():
    """测试Core集成EventBus"""
    print("\n=== 测试Core集成EventBus ===")

    # 创建EventBus
    event_bus = EventBus()

    # 创建Core（使用虚拟参数，只测试EventBus功能）
    core = AmaidesuCore(
        platform="test",
        maicore_host="localhost",
        maicore_port=8080,
        event_bus=event_bus
    )

    # 验证Core的event_bus属性
    assert core.event_bus is event_bus, "Core的event_bus属性应该返回传入的event_bus"

    print("√ Core集成EventBus测试通过")


async def test_plugin_communication():
    """测试插件间事件通信"""
    print("\n=== 测试插件间事件通信 ===")

    # 创建EventBus
    event_bus = EventBus()

    # 创建Core（使用虚拟参数）
    core = AmaidesuCore(
        platform="test",
        maicore_host="localhost",
        maicore_port=8080,
        event_bus=event_bus
    )

    # 创建插件
    plugin1 = TestPlugin1(core, {"name": "plugin1"})
    plugin2 = TestPlugin2(core, {"name": "plugin2"})
    plugin3 = TestPlugin3(core, {"name": "plugin3"})

    # 设置插件
    await plugin1.setup()
    await plugin2.setup()
    await plugin3.setup()

    # 验证插件可以访问EventBus
    assert plugin1.event_bus is event_bus
    assert plugin2.event_bus is event_bus
    assert plugin3.event_bus is event_bus

    # 触发事件
    await plugin1.trigger_event()
    await plugin1.trigger_event()

    # 等待事件处理
    await asyncio.sleep(0.2)

    # 验证插件2收到事件
    assert len(plugin2.received_events) == 2, f"插件2应该收到2个事件，实际收到 {len(plugin2.received_events)}"
    assert plugin2.received_events[0]["data"]["count"] == 1
    assert plugin2.received_events[1]["data"]["count"] == 2

    print("√ 插件间事件通信测试通过")


async def test_no_eventbus():
    """测试没有EventBus的情况"""
    print("\n=== 测试没有EventBus的情况 ===")

    # 创建Core（不传入EventBus）
    core = AmaidesuCore(
        platform="test",
        maicore_host="localhost",
        maicore_port=8080,
        event_bus=None
    )

    # 创建插件
    plugin = TestPlugin1(core, {"name": "plugin1"})

    # 验证插件的event_bus为None
    assert plugin.event_bus is None

    # 调用事件方法应该不会报错
    await plugin.emit_event("test.event", {"data": "test"})

    print("√ 没有EventBus的情况测试通过")


async def test_eventbus_cleanup():
    """测试EventBus清理功能"""
    print("\n=== 测试EventBus清理功能 ===")

    event_bus = EventBus()

    def handler(event_name, data, source):
        pass

    # 注册监听器
    event_bus.on("test.event", handler)

    # 验证监听器已注册
    assert event_bus.get_listeners_count("test.event") == 1

    # 取消监听器
    event_bus.off("test.event", handler)

    # 验证监听器已移除
    assert event_bus.get_listeners_count("test.event") == 0

    # 清除所有监听器
    event_bus.on("event1", handler)
    event_bus.on("event2", handler)

    assert len(event_bus.list_events()) == 2

    event_bus.clear()
    assert len(event_bus.list_events()) == 0

    print("√ EventBus清理功能测试通过")


async def main():
    """运行所有测试"""
    print("开始事件系统测试...")

    try:
        await test_event_bus_only()
        await test_core_with_eventbus()
        await test_plugin_communication()
        await test_no_eventbus()
        await test_eventbus_cleanup()

        print("\n[SUCCESS] 所有测试通过！")

    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())