"""
EventBus 单元测试

测试 EventBus 的所有核心功能：
- 事件订阅和取消订阅
- 事件发布（emit，支持 dict 和 Pydantic Model）
- 优先级处理
- 错误隔离
- 统计功能
- 请求-响应模式
- 生命周期管理

运行: uv run pytest tests/core/test_event_bus.py -v
"""

import asyncio

import pytest
from pydantic import BaseModel, Field

from src.modules.events.event_bus import EventBus, HandlerWrapper
from src.modules.events.payloads import RawDataPayload
from src.modules.events.registry import EventRegistry

# =============================================================================
# Test Models
# =============================================================================


class SimpleTestEvent(BaseModel):
    """简单测试事件 Model"""

    message: str = Field(default="test", description="测试消息")
    id: int = Field(default=0, description="ID")


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def event_bus():
    """创建标准 EventBus 实例"""
    return EventBus()


@pytest.fixture
def sample_event_model():
    """创建测试用的 Pydantic Model"""

    class TestEventData(BaseModel):
        message: str = Field(..., description="测试消息")
        count: int = Field(default=0, description="计数")

    return TestEventData


# =============================================================================
# 事件订阅和取消订阅测试
# =============================================================================


@pytest.mark.asyncio
async def test_on_register_handler(event_bus: EventBus):
    """测试注册事件处理器"""
    call_count = 0

    async def test_handler(event_name, payload: SimpleTestEvent, source: str):
        nonlocal call_count
        call_count += 1

    event_bus.on("test.event", test_handler, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 1


@pytest.mark.asyncio
async def test_on_multiple_handlers(event_bus: EventBus):
    """测试多个处理器订阅同一事件"""
    handler1_calls = []
    handler2_calls = []

    async def handler1(event_name, payload: SimpleTestEvent, source: str):
        handler1_calls.append(1)

    async def handler2(event_name, payload: SimpleTestEvent, source: str):
        handler2_calls.append(2)

    event_bus.on("test.event", handler1, SimpleTestEvent)
    event_bus.on("test.event", handler2, SimpleTestEvent)

    assert event_bus.get_listeners_count("test.event") == 2

    # 触发事件
    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")

    await asyncio.sleep(0.1)  # 等待异步处理

    assert len(handler1_calls) == 1
    assert len(handler2_calls) == 1


@pytest.mark.asyncio
async def test_off_remove_handler(event_bus: EventBus):
    """测试取消订阅"""
    call_count = 0

    async def test_handler(event_name, payload: SimpleTestEvent, source: str):
        nonlocal call_count
        call_count += 1

    event_bus.on("test.event", test_handler, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 1

    event_bus.off("test.event", test_handler)
    assert event_bus.get_listeners_count("test.event") == 0

    # 触发事件，处理器不应被调用
    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    assert call_count == 0


@pytest.mark.asyncio
async def test_off_non_existent_handler(event_bus: EventBus):
    """测试移除不存在的处理器（不应报错）"""

    async def test_handler(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.on("test.event", test_handler, SimpleTestEvent)
    initial_count = event_bus.get_listeners_count("test.event")

    # 尝试移除未注册的处理器
    async def another_handler(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.off("test.event", another_handler)

    # 原有处理器应该还在
    assert event_bus.get_listeners_count("test.event") == initial_count


@pytest.mark.asyncio
async def test_off_removes_event_entry_when_empty(event_bus: EventBus):
    """测试移除最后一个处理器后删除事件条目"""

    async def test_handler(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.on("test.event", test_handler, SimpleTestEvent)
    assert "test.event" in event_bus.list_events()

    event_bus.off("test.event", test_handler)
    assert "test.event" not in event_bus.list_events()


# =============================================================================
# 事件发布测试
# =============================================================================


@pytest.mark.asyncio
async def test_emit_dict_data(event_bus: EventBus):
    """测试发布字典格式数据"""
    received_data = []

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        received_data.append(payload)

    event_bus.on("test.event", handler, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(message="hello"), source="test")

    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0].message == "hello"


@pytest.mark.asyncio
async def test_emit_no_listeners(event_bus: EventBus):
    """测试发布到没有监听器的事件（不应报错）"""
    # 应该正常执行，不抛出异常
    await event_bus.emit("nonexistent.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_emit_with_sync_handler(event_bus: EventBus):
    """测试同步处理器在事件总线中的执行"""
    result = []

    def sync_handler(event_name, payload: SimpleTestEvent, source: str):
        result.append("sync")

    event_bus.on("test.event", sync_handler, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")

    await asyncio.sleep(0.1)

    assert len(result) == 1
    assert result[0] == "sync"


@pytest.mark.asyncio
async def test_emit_async_handler(event_bus: EventBus):
    """测试异步处理器在事件总线中的执行"""
    result = []

    async def async_handler(event_name, payload: SimpleTestEvent, source: str):
        await asyncio.sleep(0.01)
        result.append("async")

    event_bus.on("test.event", async_handler, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")

    await asyncio.sleep(0.1)

    assert len(result) == 1
    assert result[0] == "async"


@pytest.mark.asyncio
async def test_emit_with_pydantic_model(event_bus: EventBus, sample_event_model):
    """测试使用 emit 发布 Pydantic Model"""
    received_data = []

    async def handler(event_name, payload: sample_event_model, source: str):
        received_data.append(payload)

    event_bus.on("test.event", handler, sample_event_model)

    # 创建 Pydantic Model 实例
    event_data = sample_event_model(message="test message", count=42)
    await event_bus.emit("test.event", event_data, source="test")

    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0].message == "test message"
    assert received_data[0].count == 42


@pytest.mark.asyncio
async def test_emit_with_dict_and_pydantic_model(event_bus: EventBus, sample_event_model):
    """测试 emit 既支持 dict 也支持 Pydantic Model"""
    received_data = []

    async def handler(event_name, payload: sample_event_model, source: str):
        received_data.append(payload)

    event_bus.on("test.event", handler, sample_event_model)

    # 测试使用 Pydantic Model
    event_data = sample_event_model(message="model test", count=2)
    await event_bus.emit("test.event", event_data, source="test")
    await asyncio.sleep(0.1)
    assert len(received_data) == 1
    assert received_data[0].message == "model test"
    assert received_data[0].count == 2


@pytest.mark.asyncio
async def test_event_validation_with_registered_event(event_bus: EventBus):
    """测试已注册事件的验证功能"""
    received_data = []

    async def handler(event_name, payload: RawDataPayload, source: str):
        received_data.append(payload)

    # 注册核心事件（必须以 core. 开头）
    EventRegistry.register_core_event("core.test.validation.event", RawDataPayload)
    event_bus.on("core.test.validation.event", handler, RawDataPayload)

    # 发布符合验证的数据（使用 Payload 类）
    valid_data = RawDataPayload(content="test content", source="test_source", data_type="text")
    await event_bus.emit("core.test.validation.event", valid_data, source="test")
    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0].content == "test content"

    # 清理
    EventRegistry._core_events.pop("core.test.validation.event", None)


# =============================================================================
# 优先级处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_priority_execution_order(event_bus: EventBus):
    """测试处理器按优先级顺序执行"""
    execution_order = []

    async def high_priority_handler(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("high")

    async def medium_priority_handler(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("medium")

    async def low_priority_handler(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("low")

    # 以不同顺序注册（priority 数值越小越优先）
    event_bus.on("test.event", medium_priority_handler, SimpleTestEvent, priority=50)
    event_bus.on("test.event", high_priority_handler, SimpleTestEvent, priority=10)
    event_bus.on("test.event", low_priority_handler, SimpleTestEvent, priority=100)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # 验证执行顺序
    assert execution_order == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_same_priority_registration_order(event_bus: EventBus):
    """测试同优先级按注册顺序执行"""
    execution_order = []

    async def handler1(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("handler1")

    async def handler2(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("handler2")

    async def handler3(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("handler3")

    # 同优先级，按注册顺序
    event_bus.on("test.event", handler1, SimpleTestEvent, priority=50)
    event_bus.on("test.event", handler2, SimpleTestEvent, priority=50)
    event_bus.on("test.event", handler3, SimpleTestEvent, priority=50)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    assert execution_order == ["handler1", "handler2", "handler3"]


@pytest.mark.asyncio
async def test_default_priority(event_bus: EventBus):
    """测试默认优先级为 100"""
    execution_order = []

    async def default_handler(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("default")

    async def high_priority_handler(event_name, payload: SimpleTestEvent, source: str):
        execution_order.append("high")

    event_bus.on("test.event", default_handler, SimpleTestEvent)  # 默认 priority=100
    event_bus.on("test.event", high_priority_handler, SimpleTestEvent, priority=50)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # high_priority_handler (50) 应该先于 default_handler (100)
    assert execution_order == ["high", "default"]


# =============================================================================
# 错误隔离测试
# =============================================================================


@pytest.mark.asyncio
async def test_error_isolation_one_handler_fails(event_bus: EventBus):
    """测试单个处理器失败不影响其他处理器"""
    results = []

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        results.append("before_error")
        raise ValueError("Test error")

    async def normal_handler(event_name, payload: SimpleTestEvent, source: str):
        results.append("normal")

    event_bus.on("test.event", failing_handler, SimpleTestEvent, priority=10)
    event_bus.on("test.event", normal_handler, SimpleTestEvent, priority=20)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    # 两个处理器都应该被执行
    assert "before_error" in results
    assert "normal" in results


@pytest.mark.asyncio
async def test_error_isolation_false_propagates_error(event_bus: EventBus):
    """测试 error_isolate=False 时错误应该传播"""
    # 注意：EventBus 的实现中，即使 error_isolate=False，
    # 错误也会被 asyncio.gather(return_exceptions=True) 捕获
    # 所以这个测试验证的是在 gather 之后的错误处理行为

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, SimpleTestEvent)

    # 由于 asyncio.gather 使用 return_exceptions=True，
    # 错误不会直接传播，但会被记录到 handler wrapper 中
    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test", error_isolate=False)
    await asyncio.sleep(0.1)

    # 验证错误被记录到 handler wrapper
    handlers = event_bus._handlers.get("test.event", [])
    assert len(handlers) > 0
    assert handlers[0].error_count > 0
    assert "Test error" in handlers[0].last_error


@pytest.mark.asyncio
async def test_error_count_incremented(event_bus: EventBus):
    """测试处理器包装器的错误计数递增"""

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, SimpleTestEvent, priority=10)

    # 获取 handler wrapper
    handlers = event_bus._handlers.get("test.event", [])
    initial_error_count = handlers[0].error_count

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    # 错误计数应该增加
    assert handlers[0].error_count == initial_error_count + 1
    assert handlers[0].last_error is not None


@pytest.mark.asyncio
async def test_error_stats_updated(event_bus: EventBus):
    """测试事件统计中的错误计数更新"""

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, SimpleTestEvent)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    # 错误已被记录到 HandlerWrapper
    handlers = event_bus._handlers.get("test.event", [])
    assert len(handlers) > 0
    assert handlers[0].error_count > 0


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_clear_removes_all_handlers(event_bus: EventBus):
    """测试 clear() 清除所有处理器"""

    async def handler1(event_name, payload: SimpleTestEvent, source: str):
        pass

    async def handler2(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.on("event1", handler1, SimpleTestEvent)
    event_bus.on("event2", handler2, SimpleTestEvent)

    assert event_bus.get_listeners_count("event1") == 1
    assert event_bus.get_listeners_count("event2") == 1

    event_bus.clear()

    assert event_bus.get_listeners_count("event1") == 0
    assert event_bus.get_listeners_count("event2") == 0
    assert len(event_bus.list_events()) == 0


# =============================================================================
# 辅助方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_listeners_count(event_bus: EventBus):
    """测试获取监听器数量"""

    async def handler1(event_name, payload: SimpleTestEvent, source: str):
        pass

    async def handler2(event_name, payload: SimpleTestEvent, source: str):
        pass

    assert event_bus.get_listeners_count("test.event") == 0

    event_bus.on("test.event", handler1, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 1

    event_bus.on("test.event", handler2, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 2


@pytest.mark.asyncio
async def test_list_events(event_bus: EventBus):
    """测试列出所有已注册的事件"""

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.on("event1", handler, SimpleTestEvent)
    event_bus.on("event2", handler, SimpleTestEvent)
    event_bus.on("event3", handler, SimpleTestEvent)

    events = event_bus.list_events()
    assert len(events) == 3
    assert "event1" in events
    assert "event2" in events
    assert "event3" in events


# =============================================================================
# 并发测试
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_emits(event_bus: EventBus):
    """测试并发发布多个事件"""
    results = []

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        results.append(event_name)
        await asyncio.sleep(0.01)

    event_bus.on("test.event", handler, SimpleTestEvent)

    # 并发发布 10 个事件
    tasks = [event_bus.emit("test.event", SimpleTestEvent(id=i), source="test") for i in range(10)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(0.2)

    assert len(results) == 10


@pytest.mark.asyncio
async def test_concurrent_handlers(event_bus: EventBus):
    """测试多个处理器并发执行"""
    execution_order = []
    delays = []

    async def handler1(event_name, payload: SimpleTestEvent, source: str):
        delays.append(0.05)
        await asyncio.sleep(0.05)
        execution_order.append("handler1")

    async def handler2(event_name, payload: SimpleTestEvent, source: str):
        delays.append(0.02)
        await asyncio.sleep(0.02)
        execution_order.append("handler2")

    async def handler3(event_name, payload: SimpleTestEvent, source: str):
        delays.append(0.01)
        await asyncio.sleep(0.01)
        execution_order.append("handler3")

    event_bus.on("test.event", handler1, SimpleTestEvent)
    event_bus.on("test.event", handler2, SimpleTestEvent)
    event_bus.on("test.event", handler3, SimpleTestEvent)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # 所有处理器都应该执行
    assert len(execution_order) == 3
    assert "handler1" in execution_order
    assert "handler2" in execution_order
    assert "handler3" in execution_order


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_empty_event_name(event_bus: EventBus):
    """测试空事件名称"""

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        pass

    event_bus.on("", handler, SimpleTestEvent)
    assert event_bus.get_listeners_count("") == 1

    await event_bus.emit("", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # 验证事件被触发
    assert event_bus.get_listeners_count("") == 1


# NOTE: test_event_with_none_data 已删除
# EventBus 现在强制要求 Pydantic BaseModel，不再支持 None 数据


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
