"""EventBus：订阅与退订、Pydantic 事件发布、只收 async handler、判别字段一致性、订阅者故障隔离与统计、并发"""

import asyncio

import pytest
from pydantic import BaseModel

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import GiftInfo, RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.tool_health import ToolHealthPayload
from src.modules.events.registry import EVENT_REGISTRY


class SimpleTestEvent(BaseModel):
    message: str = "test"
    id: int = 0


async def _noop(event_name, payload, source) -> None:
    return None


def _recorder(model):
    """返回 (handler, received)：handler 记录 (event_name, payload)。"""
    received: list = []

    async def handler(event_name, payload: model, source: str) -> None:
        received.append((event_name, payload))

    return handler, received


async def _settle() -> None:
    await asyncio.sleep(0.1)  # emit 对 handler 是后台派发，等其落地


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


async def test_subscribe_unsubscribe_and_introspection(event_bus: EventBus) -> None:
    handler1, got1 = _recorder(SimpleTestEvent)
    handler2, got2 = _recorder(SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 0
    event_bus.on("test.event", handler1, SimpleTestEvent)
    event_bus.on("test.event", handler2, SimpleTestEvent)
    event_bus.on("other.event", _noop, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 2 and set(event_bus.list_events()) == {
        "test.event",
        "other.event",
    }

    await event_bus.emit("test.event", SimpleTestEvent(message="hello"), source="test")
    await _settle()
    assert [p.message for _, p in got1] == [p.message for _, p in got2] == ["hello"]

    event_bus.off("test.event", _noop)  # 移除未注册的处理器不报错、不影响已有
    assert event_bus.get_listeners_count("test.event") == 2
    event_bus.off("test.event", handler1)
    await event_bus.emit("test.event", SimpleTestEvent(message="again"), source="test")
    await _settle()
    assert (len(got1), len(got2)) == (1, 2)

    event_bus.off("test.event", handler2)
    assert "test.event" not in event_bus.list_events()  # 移除最后一个处理器后事件条目删除
    event_bus.clear()
    assert len(event_bus.list_events()) == 0


async def test_emit_edge_cases(event_bus: EventBus) -> None:
    await event_bus.emit("nonexistent.event", SimpleTestEvent(), source="test")  # 无监听器不报错

    def sync_handler(event_name, payload, source) -> None:
        pass

    with pytest.raises(TypeError, match="async"):  # 同步 handler 注册时拒绝，避免阻塞事件循环
        event_bus.on("test.event", sync_handler, SimpleTestEvent)
    assert event_bus.get_listeners_count("test.event") == 0

    handler, got = _recorder(SimpleTestEvent)
    event_bus.on("", handler, SimpleTestEvent)  # 空事件名
    await event_bus.emit("", SimpleTestEvent(message="x"), source="test")
    await _settle()
    assert len(got) == 1


async def test_registered_event_validation_and_discriminant(event_bus: EventBus) -> None:
    handler, got = _recorder(RoomMessagePayload)
    EVENT_REGISTRY["core.test.validation.danmaku"] = RoomMessagePayload
    try:
        event_bus.on("core.test.validation.danmaku", handler, RoomMessagePayload)
        danmaku = RoomMessagePayload(
            message_type="danmaku", user=RoomMessageUser(id="test_source", name="测试观众"), content="test content"
        )
        await event_bus.emit("core.test.validation.danmaku", danmaku, source="test")
        await _settle()
        assert [p.content for _, p in got] == ["test content"]
    finally:
        EVENT_REGISTRY.pop("core.test.validation.danmaku", None)

    # 事件名末段必须等于判别字段值
    gift = RoomMessagePayload(message_type="gift", user=RoomMessageUser(id="1", name="x"), gift=GiftInfo(name="小星星"))
    with pytest.raises(ValueError, match=r"danmaku.*gift|gift.*danmaku"):
        await event_bus.emit("room.message.danmaku", gift, source="test")
    gift_handler, gifts = _recorder(RoomMessagePayload)
    event_bus.on("room.message.gift", gift_handler, RoomMessagePayload)
    await event_bus.emit("room.message.gift", gift, source="test")
    # 无判别字段的 payload 不做末段校验
    plain_handler, plain = _recorder(SimpleTestEvent)
    event_bus.on("anything.goes", plain_handler, SimpleTestEvent)
    await event_bus.emit("anything.goes", SimpleTestEvent(message="ok"), source="test")
    await _settle()
    assert len(gifts) == 1 and len(plain) == 1


async def test_handler_failure_is_isolated_and_counted(event_bus: EventBus) -> None:
    results: list[str] = []

    async def failing(event_name, payload: SimpleTestEvent, source: str) -> None:
        results.append("before_error")
        raise ValueError("Test error")

    async def normal(event_name, payload: SimpleTestEvent, source: str) -> None:
        results.append("normal")

    event_bus.on("test.event", failing, SimpleTestEvent)
    event_bus.on("test.event", normal, SimpleTestEvent)
    await event_bus.emit("test.event", SimpleTestEvent(), source="test")
    await _settle()

    assert set(results) == {"before_error", "normal"}  # 一个订阅者异常不影响其他
    stats = event_bus.get_stats("test.event")
    assert stats.error_count == 1 and stats.last_error_time > 0
    wrapper = event_bus._handlers["test.event"][0]  # 同时记录到 HandlerWrapper
    assert wrapper.error_count > 0 and "Test error" in (wrapper.last_error or "")


async def test_concurrent_emits_and_handlers(event_bus: EventBus) -> None:
    finished: list[str] = []

    def slow_handler(label: str, delay: float):
        async def handler(event_name, payload: SimpleTestEvent, source: str) -> None:
            await asyncio.sleep(delay)
            finished.append(label)

        return handler

    for label, delay in (("h1", 0.05), ("h2", 0.02), ("h3", 0.01)):
        event_bus.on("test.event", slow_handler(label, delay), SimpleTestEvent)
    await asyncio.gather(*[event_bus.emit("test.event", SimpleTestEvent(id=i), source="test") for i in range(10)])
    await asyncio.sleep(0.3)
    assert len(finished) == 30 and set(finished) == {"h1", "h2", "h3"}


async def test_tool_health_wildcard_matches_concrete_emit(event_bus: EventBus) -> None:
    handler, seen = _recorder(ToolHealthPayload)
    event_bus.on(CoreEvents.TOOL_HEALTH_WILDCARD, handler, ToolHealthPayload)
    payload = ToolHealthPayload(
        tool_name="maicraft_speak", provider="maicraft", state="open", failure_count=3, last_error="连接失败"
    )
    await event_bus.emit("tool.health.maicraft_speak", payload, source="ToolRegistry")
    await _settle()
    ((name, received),) = seen
    assert (name, received.tool_name, received.state) == ("tool.health.maicraft_speak", "maicraft_speak", "open")
