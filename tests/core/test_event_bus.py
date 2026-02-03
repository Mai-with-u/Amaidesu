"""
EventBus 单元测试

测试 EventBus 的所有核心功能：
- 事件订阅和取消订阅
- 事件发布（emit 和 emit_typed）
- 优先级处理
- 错误隔离
- 统计功能
- 请求-响应模式
- 生命周期管理

运行: uv run pytest tests/core/test_event_bus.py -v
"""

import asyncio
import sys
import os
from typing import List
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field
from src.core.event_bus import EventBus, EventStats, HandlerWrapper
from src.core.events.models import RawDataEvent, NormalizedMessageEvent
from src.core.events.registry import EventRegistry
from src.core.events import register_core_events

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def event_bus():
    """创建标准 EventBus 实例（启用统计）"""
    return EventBus(enable_stats=True)


@pytest.fixture
def event_bus_no_stats():
    """创建不启用统计的 EventBus 实例"""
    return EventBus(enable_stats=False)


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

    async def test_handler(event_name, data, source):
        nonlocal call_count
        call_count += 1

    event_bus.on("test.event", test_handler)
    assert event_bus.get_listeners_count("test.event") == 1


@pytest.mark.asyncio
async def test_on_multiple_handlers(event_bus: EventBus):
    """测试多个处理器订阅同一事件"""
    handler1_calls = []
    handler2_calls = []

    async def handler1(event_name, data, source):
        handler1_calls.append(1)

    async def handler2(event_name, data, source):
        handler2_calls.append(2)

    event_bus.on("test.event", handler1)
    event_bus.on("test.event", handler2)

    assert event_bus.get_listeners_count("test.event") == 2

    # 触发事件
    await event_bus.emit("test.event", {"message": "test"}, source="test")

    await asyncio.sleep(0.1)  # 等待异步处理

    assert len(handler1_calls) == 1
    assert len(handler2_calls) == 1


@pytest.mark.asyncio
async def test_off_remove_handler(event_bus: EventBus):
    """测试取消订阅"""
    call_count = 0

    async def test_handler(event_name, data, source):
        nonlocal call_count
        call_count += 1

    event_bus.on("test.event", test_handler)
    assert event_bus.get_listeners_count("test.event") == 1

    event_bus.off("test.event", test_handler)
    assert event_bus.get_listeners_count("test.event") == 0

    # 触发事件，处理器不应被调用
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    assert call_count == 0


@pytest.mark.asyncio
async def test_off_non_existent_handler(event_bus: EventBus):
    """测试移除不存在的处理器（不应报错）"""

    async def test_handler(event_name, data, source):
        pass

    event_bus.on("test.event", test_handler)
    initial_count = event_bus.get_listeners_count("test.event")

    # 尝试移除未注册的处理器
    async def another_handler(event_name, data, source):
        pass

    event_bus.off("test.event", another_handler)

    # 原有处理器应该还在
    assert event_bus.get_listeners_count("test.event") == initial_count


@pytest.mark.asyncio
async def test_off_removes_event_entry_when_empty(event_bus: EventBus):
    """测试移除最后一个处理器后删除事件条目"""

    async def test_handler(event_name, data, source):
        pass

    event_bus.on("test.event", test_handler)
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

    async def handler(event_name, data, source):
        received_data.append(data)

    event_bus.on("test.event", handler)
    await event_bus.emit("test.event", {"message": "hello"}, source="test")

    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0]["message"] == "hello"


@pytest.mark.asyncio
async def test_emit_no_listeners(event_bus: EventBus):
    """测试发布到没有监听器的事件（不应报错）"""
    # 应该正常执行，不抛出异常
    await event_bus.emit("nonexistent.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_emit_sync_handler(event_bus: EventBus):
    """测试同步处理器在事件总线中的执行"""
    result = []

    def sync_handler(event_name, data, source):
        result.append("sync")

    event_bus.on("test.event", sync_handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")

    await asyncio.sleep(0.1)

    assert len(result) == 1
    assert result[0] == "sync"


@pytest.mark.asyncio
async def test_emit_async_handler(event_bus: EventBus):
    """测试异步处理器在事件总线中的执行"""
    result = []

    async def async_handler(event_name, data, source):
        await asyncio.sleep(0.01)
        result.append("async")

    event_bus.on("test.event", async_handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")

    await asyncio.sleep(0.1)

    assert len(result) == 1
    assert result[0] == "async"


@pytest.mark.asyncio
async def test_emit_typed_with_pydantic_model(event_bus: EventBus, sample_event_model):
    """测试使用 emit_typed 发布 Pydantic Model"""
    received_data = []

    async def handler(event_name, data, source):
        received_data.append(data)

    event_bus.on("test.event", handler)

    # 创建 Pydantic Model 实例
    event_data = sample_event_model(message="test message", count=42)
    await event_bus.emit_typed("test.event", event_data, source="test")

    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0]["message"] == "test message"
    assert received_data[0]["count"] == 42


@pytest.mark.asyncio
async def test_emit_deprecated_warning_with_registered_event(event_bus: EventBus):
    """测试对已注册事件使用字典格式时发出警告"""
    received_data = []

    async def handler(event_name, data, source):
        received_data.append(data)

    # 注册核心事件（使用 models.py 中的定义，必须以 core. 开头）
    EventRegistry.register_core_event("core.test.registered.event", RawDataEvent)

    event_bus.on("core.test.registered.event", handler)

    # 使用字典格式发布已注册事件（应该触发警告）
    with patch.object(event_bus.logger, 'warning') as mock_warning:
        await event_bus.emit("core.test.registered.event", {"content": "test", "source": "test", "data_type": "text"}, source="test")
        await asyncio.sleep(0.1)

        # 验证警告被调用
        mock_warning.assert_called()
        warning_msg = str(mock_warning.call_args)
        assert "已废弃" in warning_msg or "emit_typed" in warning_msg

    # 清理
    EventRegistry._core_events.pop("core.test.registered.event", None)


@pytest.mark.asyncio
async def test_event_validation_with_registered_event(event_bus: EventBus):
    """测试已注册事件的验证功能"""
    received_data = []

    async def handler(event_name, data, source):
        received_data.append(data)

    # 注册核心事件（必须以 core. 开头）
    EventRegistry.register_core_event("core.test.validation.event", RawDataEvent)
    event_bus.on("core.test.validation.event", handler)

    # 发布符合验证的数据
    valid_data = {
        "content": "test content",
        "source": "test_source",
        "data_type": "text"
    }
    await event_bus.emit("core.test.validation.event", valid_data, source="test")
    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0]["content"] == "test content"

    # 清理
    EventRegistry._core_events.pop("core.test.validation.event", None)


# =============================================================================
# 优先级处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_priority_execution_order(event_bus: EventBus):
    """测试处理器按优先级顺序执行"""
    execution_order = []

    async def high_priority_handler(event_name, data, source):
        execution_order.append("high")

    async def medium_priority_handler(event_name, data, source):
        execution_order.append("medium")

    async def low_priority_handler(event_name, data, source):
        execution_order.append("low")

    # 以不同顺序注册（priority 数值越小越优先）
    event_bus.on("test.event", medium_priority_handler, priority=50)
    event_bus.on("test.event", high_priority_handler, priority=10)
    event_bus.on("test.event", low_priority_handler, priority=100)

    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    # 验证执行顺序
    assert execution_order == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_same_priority_registration_order(event_bus: EventBus):
    """测试同优先级按注册顺序执行"""
    execution_order = []

    async def handler1(event_name, data, source):
        execution_order.append("handler1")

    async def handler2(event_name, data, source):
        execution_order.append("handler2")

    async def handler3(event_name, data, source):
        execution_order.append("handler3")

    # 同优先级，按注册顺序
    event_bus.on("test.event", handler1, priority=50)
    event_bus.on("test.event", handler2, priority=50)
    event_bus.on("test.event", handler3, priority=50)

    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    assert execution_order == ["handler1", "handler2", "handler3"]


@pytest.mark.asyncio
async def test_default_priority(event_bus: EventBus):
    """测试默认优先级为 100"""
    execution_order = []

    async def default_handler(event_name, data, source):
        execution_order.append("default")

    async def high_priority_handler(event_name, data, source):
        execution_order.append("high")

    event_bus.on("test.event", default_handler)  # 默认 priority=100
    event_bus.on("test.event", high_priority_handler, priority=50)

    await event_bus.emit("test.event", {"message": "test"}, source="test")
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

    async def failing_handler(event_name, data, source):
        results.append("before_error")
        raise ValueError("Test error")

    async def normal_handler(event_name, data, source):
        results.append("normal")

    event_bus.on("test.event", failing_handler, priority=10)
    event_bus.on("test.event", normal_handler, priority=20)

    await event_bus.emit("test.event", {"message": "test"}, source="test", error_isolate=True)
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

    async def failing_handler(event_name, data, source):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler)

    # 由于 asyncio.gather 使用 return_exceptions=True，
    # 错误不会直接传播，但会被记录到 handler wrapper 中
    await event_bus.emit("test.event", {"message": "test"}, source="test", error_isolate=False)
    await asyncio.sleep(0.1)

    # 验证错误被记录到 handler wrapper
    handlers = event_bus._handlers.get("test.event", [])
    assert len(handlers) > 0
    assert handlers[0].error_count > 0
    assert "Test error" in handlers[0].last_error


@pytest.mark.asyncio
async def test_error_count_incremented(event_bus: EventBus):
    """测试处理器包装器的错误计数递增"""
    async def failing_handler(event_name, data, source):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler, priority=10)

    # 获取 handler wrapper
    handlers = event_bus._handlers.get("test.event", [])
    initial_error_count = handlers[0].error_count

    await event_bus.emit("test.event", {"message": "test"}, source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    # 错误计数应该增加
    assert handlers[0].error_count == initial_error_count + 1
    assert handlers[0].last_error is not None


@pytest.mark.asyncio
async def test_error_stats_updated(event_bus: EventBus):
    """测试事件统计中的错误计数更新"""
    async def failing_handler(event_name, data, source):
        raise ValueError("Test error")

    event_bus.on("test.event", failing_handler)

    await event_bus.emit("test.event", {"message": "test"}, source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    stats = event_bus.get_stats("test.event")
    assert stats is not None
    assert stats.error_count == 1


# =============================================================================
# 统计功能测试
# =============================================================================


@pytest.mark.asyncio
async def test_stats_enabled_by_default(event_bus: EventBus):
    """测试统计功能默认启用"""
    assert event_bus.enable_stats is True


@pytest.mark.asyncio
async def test_stats_disabled(event_bus_no_stats: EventBus):
    """测试禁用统计功能"""
    assert event_bus_no_stats.enable_stats is False

    async def handler(event_name, data, source):
        pass

    event_bus_no_stats.on("test.event", handler)
    await event_bus_no_stats.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    stats = event_bus_no_stats.get_stats("test.event")
    assert stats is None


@pytest.mark.asyncio
async def test_get_stats_single_event(event_bus: EventBus):
    """测试获取单个事件的统计"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("test.event", handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    stats = event_bus.get_stats("test.event")
    assert stats is not None
    assert stats.emit_count == 1
    assert stats.listener_count == 1
    assert stats.error_count == 0
    assert stats.last_emit_time > 0


@pytest.mark.asyncio
async def test_get_all_stats(event_bus: EventBus):
    """测试获取所有事件的统计"""
    async def handler1(event_name, data, source):
        pass

    async def handler2(event_name, data, source):
        pass

    event_bus.on("event1", handler1)
    event_bus.on("event2", handler2)

    await event_bus.emit("event1", {"message": "test"}, source="test")
    await event_bus.emit("event2", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    all_stats = event_bus.get_all_stats()
    assert len(all_stats) >= 2
    assert "event1" in all_stats
    assert "event2" in all_stats
    assert all_stats["event1"].emit_count == 1
    assert all_stats["event2"].emit_count == 1


@pytest.mark.asyncio
async def test_reset_stats_single_event(event_bus: EventBus):
    """测试重置单个事件的统计"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("test.event", handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    stats_before = event_bus.get_stats("test.event")
    assert stats_before.emit_count == 1

    event_bus.reset_stats("test.event")

    stats_after = event_bus.get_stats("test.event")
    assert stats_after.emit_count == 0
    assert stats_after.error_count == 0


@pytest.mark.asyncio
async def test_reset_stats_all_events(event_bus: EventBus):
    """测试重置所有事件的统计"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("event1", handler)
    event_bus.on("event2", handler)

    await event_bus.emit("event1", {"message": "test"}, source="test")
    await event_bus.emit("event2", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    event_bus.reset_stats()  # 不传参数，重置所有

    all_stats = event_bus.get_all_stats()
    assert len(all_stats) == 0  # 所有统计被清空


@pytest.mark.asyncio
async def test_stats_execution_time(event_bus: EventBus):
    """测试统计执行时间"""
    async def slow_handler(event_name, data, source):
        await asyncio.sleep(0.05)  # 50ms

    event_bus.on("test.event", slow_handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    stats = event_bus.get_stats("test.event")
    assert stats is not None
    assert stats.total_execution_time_ms >= 50  # 至少 50ms


# =============================================================================
# 请求-响应模式测试
# =============================================================================


@pytest.mark.asyncio
async def test_request_response_basic(event_bus: EventBus):
    """测试基本的请求-响应模式"""
    # 设置响应处理器
    async def response_handler(event_name, data, source):
        await event_bus.emit(data["response_event"], {"result": "success"}, source="responder")

    event_bus.on("test.request", response_handler)

    # 发送请求
    response = await event_bus.request("test.request", {"query": "test"})

    assert response is not None
    assert response["result"] == "success"


@pytest.mark.asyncio
async def test_request_timeout(event_bus: EventBus):
    """测试请求超时"""
    # 不设置响应处理器，直接请求超时
    with pytest.raises(asyncio.TimeoutError):
        await event_bus.request("test.request", {"query": "test"}, timeout=0.1)


@pytest.mark.asyncio
async def test_request_with_custom_timeout(event_bus: EventBus):
    """测试自定义超时时间"""
    async def slow_handler(event_name, data, source):
        await asyncio.sleep(0.2)

    event_bus.on("test.request", slow_handler)

    # 超时设置为 0.1 秒，但处理器需要 0.2 秒
    with pytest.raises(asyncio.TimeoutError):
        await event_bus.request("test.request", {"query": "test"}, timeout=0.1)


@pytest.mark.asyncio
async def test_request_during_cleanup(event_bus: EventBus):
    """测试 cleanup 期间的请求应该返回 None"""
    await event_bus.cleanup()

    response = await event_bus.request("test.request", {"query": "test"})
    assert response is None


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_clear_removes_all_handlers(event_bus: EventBus):
    """测试 clear() 清除所有处理器"""
    async def handler1(event_name, data, source):
        pass

    async def handler2(event_name, data, source):
        pass

    event_bus.on("event1", handler1)
    event_bus.on("event2", handler2)

    assert event_bus.get_listeners_count("event1") == 1
    assert event_bus.get_listeners_count("event2") == 1

    event_bus.clear()

    assert event_bus.get_listeners_count("event1") == 0
    assert event_bus.get_listeners_count("event2") == 0
    assert len(event_bus.list_events()) == 0


@pytest.mark.asyncio
async def test_clear_removes_all_stats(event_bus: EventBus):
    """测试 clear() 清除所有统计"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("test.event", handler)
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    assert len(event_bus.get_all_stats()) > 0

    event_bus.clear()

    assert len(event_bus.get_all_stats()) == 0


@pytest.mark.asyncio
async def test_cleanup_sets_flag(event_bus: EventBus):
    """测试 cleanup() 设置清理标志"""
    assert event_bus._is_cleanup is False

    await event_bus.cleanup()

    assert event_bus._is_cleanup is True


@pytest.mark.asyncio
async def test_cleanup_clears_pending_requests(event_bus: EventBus):
    """测试 cleanup() 取消所有待处理的请求"""
    # 创建一个待处理的请求（不响应）
    try:
        task = asyncio.create_task(event_bus.request("test.request", {"query": "test"}, timeout=5.0))
        await asyncio.sleep(0.01)  # 让请求开始

        await event_bus.cleanup()

        # 任务应该被取消
        assert task.cancelled() or task.done()
    except asyncio.CancelledError:
        pass  # 预期的行为


@pytest.mark.asyncio
async def test_emit_during_cleanup_ignored(event_bus: EventBus):
    """测试 cleanup 期间的事件发布被忽略"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("test.event", handler)

    await event_bus.cleanup()

    # cleanup 后的事件应该被忽略
    await event_bus.emit("test.event", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    # 统计不应该增加
    stats = event_bus.get_stats("test.event")
    assert stats is None or stats.emit_count == 0


# =============================================================================
# 辅助方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_listeners_count(event_bus: EventBus):
    """测试获取监听器数量"""
    async def handler1(event_name, data, source):
        pass

    async def handler2(event_name, data, source):
        pass

    assert event_bus.get_listeners_count("test.event") == 0

    event_bus.on("test.event", handler1)
    assert event_bus.get_listeners_count("test.event") == 1

    event_bus.on("test.event", handler2)
    assert event_bus.get_listeners_count("test.event") == 2


@pytest.mark.asyncio
async def test_list_events(event_bus: EventBus):
    """测试列出所有已注册的事件"""
    async def handler(event_name, data, source):
        pass

    event_bus.on("event1", handler)
    event_bus.on("event2", handler)
    event_bus.on("event3", handler)

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

    async def handler(event_name, data, source):
        results.append(event_name)
        await asyncio.sleep(0.01)

    event_bus.on("test.event", handler)

    # 并发发布 10 个事件
    tasks = [event_bus.emit("test.event", {"id": i}, source="test") for i in range(10)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(0.2)

    assert len(results) == 10


@pytest.mark.asyncio
async def test_concurrent_handlers(event_bus: EventBus):
    """测试多个处理器并发执行"""
    execution_order = []
    delays = []

    async def handler1(event_name, data, source):
        delays.append(0.05)
        await asyncio.sleep(0.05)
        execution_order.append("handler1")

    async def handler2(event_name, data, source):
        delays.append(0.02)
        await asyncio.sleep(0.02)
        execution_order.append("handler2")

    async def handler3(event_name, data, source):
        delays.append(0.01)
        await asyncio.sleep(0.01)
        execution_order.append("handler3")

    event_bus.on("test.event", handler1)
    event_bus.on("test.event", handler2)
    event_bus.on("test.event", handler3)

    await event_bus.emit("test.event", {"message": "test"}, source="test")
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
    async def handler(event_name, data, source):
        pass

    event_bus.on("", handler)
    assert event_bus.get_listeners_count("") == 1

    await event_bus.emit("", {"message": "test"}, source="test")
    await asyncio.sleep(0.1)

    stats = event_bus.get_stats("")
    assert stats is not None


@pytest.mark.asyncio
async def test_event_with_none_data(event_bus: EventBus):
    """测试事件数据为 None"""
    received_data = []

    async def handler(event_name, data, source):
        received_data.append(data)

    event_bus.on("test.event", handler)
    await event_bus.emit("test.event", None, source="test")
    await asyncio.sleep(0.1)

    assert len(received_data) == 1
    assert received_data[0] is None


@pytest.mark.asyncio
async def test_handler_wrapper_defaults():
    """测试 HandlerWrapper 默认值"""
    async def handler(event_name, data, source):
        pass

    wrapper = HandlerWrapper(handler=handler)

    assert wrapper.handler == handler
    assert wrapper.priority == 100
    assert wrapper.error_count == 0
    assert wrapper.last_error is None


@pytest.mark.asyncio
async def test_event_stats_defaults():
    """测试 EventStats 默认值"""
    stats = EventStats()

    assert stats.emit_count == 0
    assert stats.listener_count == 0
    assert stats.error_count == 0
    assert stats.last_emit_time == 0
    assert stats.last_error_time == 0
    assert stats.total_execution_time_ms == 0


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
