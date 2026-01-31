"""
Phase 1 基础设施测试

包含:
- Provider接口测试
- EventBus增强测试
- DataCache测试
"""

import pytest
import asyncio
from src.core.providers import RawData, RenderParameters, CanonicalMessage


class TestProviderDataStructures:
    """测试Provider数据类"""

    def test_raw_data_creation(self):
        """测试RawData创建"""
        data = RawData(content="hello", source="test", data_type="text", timestamp=1234567890)
        assert data.content == "hello"
        assert data.source == "test"
        assert data.data_type == "text"
        assert data.preserve_original is False
        assert data.metadata == {}

    def test_raw_data_with_metadata(self):
        """测试RawData带元数据"""
        data = RawData(
            content={"key": "value"}, source="test", data_type="json", timestamp=1234567890, metadata={"extra": "info"}
        )
        assert data.metadata == {"extra": "info"}

    def test_render_parameters_creation(self):
        """测试RenderParameters创建"""
        params = RenderParameters(content="world", render_type="text")
        assert params.content == "world"
        assert params.priority == 100

    def test_canonical_message_creation(self):
        """测试CanonicalMessage创建"""
        msg = CanonicalMessage(text="hello world", source="test")
        assert msg.text == "hello world"
        assert msg.source == "test"
        assert msg.metadata == {}


class TestEventBusBasic:
    """测试EventBus基础功能"""

    @pytest.mark.asyncio
    async def test_emit_and_receive(self):
        """测试发布和接收"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        received = []

        async def handler(event_name, data, source):
            received.append((event_name, data, source))

        event_bus.on("test.event", handler)
        await event_bus.emit("test.event", "data", "test_source")

        assert len(received) == 1
        assert received[0] == ("test.event", "data", "test_source")


class TestEventBusAdvanced:
    """测试EventBus高级功能"""

    @pytest.mark.asyncio
    async def test_priority_control(self):
        """测试优先级控制"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        execution_order = []

        # 注册三个不同优先级的处理器
        async def high_priority(event_name, data, source):
            execution_order.append("high")

        async def medium_priority(event_name, data, source):
            execution_order.append("medium")

        async def low_priority(event_name, data, source):
            execution_order.append("low")

        # 先注册中优先级
        event_bus.on("test.event", medium_priority, priority=50)
        # 再注册高优先级（应该先执行）
        event_bus.on("test.event", high_priority, priority=10)
        # 最后注册低优先级（应该最后执行）
        event_bus.on("test.event", low_priority, priority=100)

        await event_bus.emit("test.event", "data", "test_source")

        # 验证执行顺序
        assert execution_order == ["high", "medium", "low"]

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """测试错误隔离"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        results = []

        # 注册一个会抛出异常的处理器
        async def failing_handler(event_name, data, source):
            results.append("failing_before")
            raise ValueError("Simulated error")

        # 注册一个正常的处理器
        async def normal_handler(event_name, data, source):
            results.append("normal")

        event_bus.on("test.event", failing_handler)
        event_bus.on("test.event", normal_handler)

        # 发布事件，一个处理器失败不影响另一个
        await event_bus.emit("test.event", "data", "test_source")

        # 两个处理器都应该被调用
        assert "failing_before" in results
        assert "normal" in results

    @pytest.mark.asyncio
    async def test_statistics_tracking(self):
        """测试统计信息跟踪"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=True)

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)

        # 发布10个事件
        for i in range(10):
            await event_bus.emit("test.event", f"data{i}", "source")

        # 发布一些其他事件
        await event_bus.emit("other.event", "data", "source")

        stats = event_bus.get_all_stats()

        # 验证统计信息
        assert stats["test.event"].emit_count == 10
        assert stats["test.event"].listener_count == 1
        assert stats["test.event"].error_count == 0  # 没有错误

    @pytest.mark.asyncio
    async def test_statistics_with_errors(self):
        """测试错误统计"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=True)

        async def failing_handler(event_name, data, source):
            raise RuntimeError("Test error")

        async def normal_handler(event_name, data, source):
            pass

        event_bus.on("test.event", failing_handler)
        event_bus.on("test.event", normal_handler)

        # 发布10个事件
        for i in range(10):
            await event_bus.emit("test.event", f"data{i}", "source")

        stats = event_bus.get_all_stats()
        event_stats = stats["test.event"]

        # 验证错误统计
        assert event_stats.emit_count == 10
        assert event_stats.listener_count == 2
        assert event_stats.error_count == 10  # 每次都失败一个处理器

    @pytest.mark.asyncio
    async def test_lifecycle_cleanup(self):
        """测试生命周期清理"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)
        assert len(event_bus._handlers["test.event"]) == 1

        # 清理所有处理器
        await event_bus.cleanup()

        # 验证所有处理器都被清理
        assert len(event_bus._handlers) == 0
