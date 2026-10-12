"""
EventBus 单元测试

测试 EventBus 的所有核心功能：
- 事件订阅和取消订阅
- 事件发布（emit，Pydantic Model）
- 并发执行互不影响（一个订阅者异常不影响其他，且错误被计数）
- 只接受 async handler
- 判别字段一致性校验（事件名末段 == 判别字段值）
- 统计功能
- 生命周期管理

运行: uv run pytest tests/modules/events/test_event_bus.py -v
"""

import asyncio

import pytest
from pydantic import BaseModel, Field

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import GiftInfo, RoomMessagePayload, RoomMessageUser
from src.modules.events.registry import EVENT_REGISTRY

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
async def test_emit_model_data(event_bus: EventBus):
    """测试发布 Pydantic Model 格式数据"""
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
async def test_emit_with_sync_handler_rejected(event_bus: EventBus):
    """同步 handler 在注册时被直接拒绝（避免阻塞事件循环）"""
    result = []

    def sync_handler(event_name, payload: SimpleTestEvent, source: str):
        result.append("sync")

    with pytest.raises(TypeError, match="async"):
        event_bus.on("test.event", sync_handler, SimpleTestEvent)

    assert event_bus.get_listeners_count("test.event") == 0


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

    async def handler(event_name, payload: RoomMessagePayload, source: str):
        received_data.append(payload)

    EVENT_REGISTRY["core.test.validation.danmaku"] = RoomMessagePayload
    event_bus.on("core.test.validation.danmaku", handler, RoomMessagePayload)

    valid_data = RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="test_source", name="测试观众"),
        content="test content",
        timestamp_ms=1706745600000,
    )
    await event_bus.emit("core.test.validation.danmaku", valid_data, source="test")
    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0].content == "test content"

    EVENT_REGISTRY.pop("core.test.validation.danmaku", None)


# =============================================================================
# 判别字段一致性校验测试
# =============================================================================


@pytest.mark.asyncio
async def test_discriminant_mismatch_raises(event_bus: EventBus):
    """事件名末段与判别字段不符时 emit 报错（RoomMessagePayload.message_type）"""
    payload = RoomMessagePayload(
        message_type="gift",
        user=RoomMessageUser(id="1", name="x"),
    )

    with pytest.raises(ValueError, match=r"danmaku.*gift|gift.*danmaku"):
        await event_bus.emit("room.message.danmaku", payload, source="test")


@pytest.mark.asyncio
async def test_discriminant_match_passes(event_bus: EventBus):
    """事件名末段与判别字段一致时正常分发"""
    received = []

    async def handler(event_name, payload: RoomMessagePayload, source: str):
        received.append(payload)

    event_bus.on("room.message.gift", handler, RoomMessagePayload)

    payload = RoomMessagePayload(
        message_type="gift",
        user=RoomMessageUser(id="1", name="x"),
        gift=GiftInfo(name="小星星"),
    )
    await event_bus.emit("room.message.gift", payload, source="test")
    await asyncio.sleep(0.1)

    assert len(received) == 1


@pytest.mark.asyncio
async def test_discriminant_check_skipped_for_plain_payload(event_bus: EventBus):
    """无判别字段的 payload 不做末段校验（事件名与字段无对应关系）"""
    received = []

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        received.append(payload)

    event_bus.on("anything.goes", handler, SimpleTestEvent)

    await event_bus.emit("anything.goes", SimpleTestEvent(message="ok"), source="test")
    await asyncio.sleep(0.1)

    assert len(received) == 1


# =============================================================================
# 并发互不影响与错误计数测试
# =============================================================================


@pytest.mark.asyncio
async def test_one_handler_failure_does_not_affect_others(event_bus: EventBus):
    """单个订阅者抛异常不影响其他订阅者执行"""
    results = []

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        results.append("before_error")
        raise ValueError("Test error")

    async def normal_handler(event_name, payload: SimpleTestEvent, source: str):
        results.append("normal")

    event_bus.on("test.event", failing_handler, SimpleTestEvent)
    event_bus.on("test.event", normal_handler, SimpleTestEvent)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # 两个处理器都应该被执行
    assert "before_error" in results
    assert "normal" in results


@pytest.mark.asyncio
async def test_handler_error_counted_in_stats(event_bus: EventBus):
    """订阅者异常被计入事件统计（error_count / last_error_time）"""

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, SimpleTestEvent)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    stats = event_bus.get_stats("test.event")
    assert stats is not None
    assert stats.error_count == 1
    assert stats.last_error_time > 0


@pytest.mark.asyncio
async def test_handler_error_recorded_on_wrapper(event_bus: EventBus):
    """订阅者异常同时记录到 HandlerWrapper（error_count / last_error）"""

    async def failing_handler(event_name, payload: SimpleTestEvent, source: str):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, SimpleTestEvent)

    await event_bus.emit("test.event", SimpleTestEvent(message="test"), source="test")
    await asyncio.sleep(0.1)

    # 错误已被记录到 HandlerWrapper
    handlers = event_bus._handlers.get("test.event", [])
    assert len(handlers) > 0
    assert handlers[0].error_count > 0
    assert "Test error" in (handlers[0].last_error or "")


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


# =============================================================================
# tool.health.# 通配路由集成测试（真实 EventBus emit → 通配分发）
# =============================================================================


@pytest.mark.asyncio
async def test_tool_health_wildcard_matches_concrete_emit(event_bus: EventBus):
    """真实 emit 路径验证：``tool.health.#`` 通配订阅能收到具体名 ``tool.health.<tool>`` 事件"""
    from src.modules.events.payloads.tool_health import ToolHealthPayload

    seen: list[tuple[str, ToolHealthPayload]] = []

    async def handler(event_name, payload: ToolHealthPayload, source: str):
        seen.append((event_name, payload))

    event_bus.on(CoreEvents.TOOL_HEALTH_WILDCARD, handler, ToolHealthPayload)

    payload = ToolHealthPayload(
        tool_name="maicraft_speak",
        provider="maicraft",
        state="open",
        failure_count=3,
        last_error="连接失败",
    )
    await event_bus.emit("tool.health.maicraft_speak", payload, source="ToolRegistry")
    await asyncio.sleep(0.1)

    assert len(seen) == 1
    event_name, received = seen[0]
    assert event_name == "tool.health.maicraft_speak"
    assert received.tool_name == "maicraft_speak"
    assert received.state == "open"
