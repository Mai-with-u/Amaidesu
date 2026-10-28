"""EventBus 通配订阅（AMQP topic 风格：``*``=单层，``#``=多层）

订阅者并发执行、无顺序语义（本文件的列表断言只在单订阅者下按到达序）。
"""

import pytest
from pydantic import BaseModel

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads import RoomMessagePayload, RoomMessageUser, ToolResultPayload


class SimpleTestEvent(BaseModel):
    message: str = "test"


def _danmaku(text: str = "hello") -> RoomMessagePayload:
    return RoomMessagePayload(message_type="danmaku", user=RoomMessageUser(id="u1", name="tester"), content=text)


def _tool_result(tool_name: str = "speak") -> ToolResultPayload:
    return ToolResultPayload(tool_name=tool_name, status="success", result={"data": "ok"})


def _subscribe(bus: EventBus, pattern: str, model=SimpleTestEvent) -> list:
    """订阅并返回按到达序记录 event_name 的列表。"""
    received: list = []

    async def handler(event_name, payload, source) -> None:
        received.append(event_name)

    bus.on(pattern, handler, model)
    return received


@pytest.fixture
def bus() -> EventBus:
    return EventBus(enable_stats=False)


async def _emit_all(bus: EventBus, names: list[str], payload=None) -> None:
    for name in names:
        await bus.emit(name, payload or SimpleTestEvent(), source="test")
    await bus.wait_until_drained()


async def test_exact_subscription_and_wildcard_fire_independently(bus: EventBus) -> None:
    got: list = []

    async def handler(event_name, payload: SimpleTestEvent, source: str) -> None:
        got.append((event_name, payload.message, source))

    bus.on("test.event", handler, SimpleTestEvent)
    exact = _subscribe(bus, "room.message.danmaku", RoomMessagePayload)
    wild = _subscribe(bus, "room.message.#", RoomMessagePayload)
    await bus.emit("test.event", SimpleTestEvent(message="hi"), source="src")
    await bus.emit("room.message.danmaku", _danmaku())
    await bus.emit("room.state.heat", SimpleTestEvent())  # 不同子层不触发 room.message.#
    await bus.wait_until_drained()

    assert got == [("test.event", "hi", "src")]
    assert exact == wild == ["room.message.danmaku"]  # 精确 + 通配订阅同一事件都触发


async def test_single_level_star_matches_exactly_one_segment(bus: EventBus) -> None:
    star = _subscribe(bus, "room.*")
    await _emit_all(bus, ["room.connected", "room.message.danmaku", "room"])
    assert star == ["room.connected"]


async def test_hash_matches_zero_or_more_segments_with_prefix_constraint(bus: EventBus) -> None:
    tool = _subscribe(bus, "tool.result.#", ToolResultPayload)
    everything = _subscribe(bus, "#")
    await bus.emit("tool.result.speak", _tool_result("speak"), source="test")
    await bus.emit("tool.result.a.b.c", _tool_result("a.b.c"), source="test")
    await bus.emit("tool.result", _tool_result("parent"), source="test")  # 父名（零段）也命中
    await _emit_all(bus, ["tool.something", "tool", "x.y.z"])

    assert tool == ["tool.result.speak", "tool.result.a.b.c", "tool.result"]
    assert everything == [
        "tool.result.speak",
        "tool.result.a.b.c",
        "tool.result",
        "tool.something",
        "tool",
        "x.y.z",
    ]


async def test_off_removes_wildcard_only(bus: EventBus) -> None:
    calls: list = []

    async def handler(event_name, payload, source) -> None:
        calls.append(event_name)

    bus.on("exact.event", handler, SimpleTestEvent)
    bus.on("wild.#", handler, SimpleTestEvent)
    bus.off("wild.#", handler)
    assert (bus.get_listeners_count("exact.event"), bus.get_listeners_count("wild.#")) == (1, 0)

    await _emit_all(bus, ["exact.event", "wild.something"])
    assert calls == ["exact.event"]


async def test_typed_subscription_over_wildcard_validates_payload(bus: EventBus) -> None:
    received: list = []

    async def typed(event_name, payload: RoomMessagePayload, source: str) -> None:
        assert isinstance(payload, RoomMessagePayload)
        received.append(payload)

    bus.on("room.message.#", typed, RoomMessagePayload)
    gift = RoomMessagePayload(message_type="gift", user=RoomMessageUser(id="u1", name="tester"))
    await bus.emit("room.message.danmaku", _danmaku("hello"))
    await bus.emit("room.message.gift", gift)
    await bus.wait_until_drained()
    assert [(p.message_type, p.content) for p in received] == [("danmaku", "hello"), ("gift", "")]

    # 形状不符：emit 不拒绝，但类型化订阅校验失败，handler 不被调用
    strict = _subscribe(bus, "tool.result.#", ToolResultPayload)
    await bus.emit("tool.result.bad", SimpleTestEvent(message="oops"))
    await bus.wait_until_drained()
    assert strict == []

    # 同族 payload 挂错事件名（末段 != 判别字段值）在 emit 期直接报错
    with pytest.raises(ValueError, match="danmaku"):
        await bus.emit("tool.result.bad", _danmaku("oops"))


async def test_stats_are_keyed_by_actual_event_name_not_pattern() -> None:
    bus = EventBus(enable_stats=True)
    _subscribe(bus, "tool.result.#")
    await _emit_all(bus, ["tool.result.speak", "tool.result.summarize_timeline"])
    for name in ("tool.result.speak", "tool.result.summarize_timeline"):
        assert bus.get_stats(name).emit_count == 1
    assert bus.get_stats("tool.result.#") is None and "tool.result.#" not in bus.get_all_stats()


@pytest.mark.parametrize(
    ("pattern", "event_name", "expected"),
    [
        ("a.b.c", "a.b.c", True),
        ("a.b.c", "a.b.x", False),
        ("a.b", "a.b.c", False),
        ("room.*", "room.message", True),
        ("room.*", "room", False),  # * 必须有 1 段
        ("room.*", "room.message.danmaku", False),
        ("tool.result.#", "tool.result", True),
        ("tool.result.#", "tool.result.x.y.z", True),
        ("tool.result.#", "tool", False),
        ("tool.result.#", "tool.x", False),
        ("#", "a.b.c.d.e", True),
        ("a.*.b.#", "a.x.b", True),
        ("a.*.b.#", "a.x.b.c.d", True),
        ("a.*.b.#", "a.x.y.b", False),
    ],
)
def test_match_wildcard(pattern: str, event_name: str, expected: bool) -> None:
    assert EventBus._match_wildcard(pattern, event_name) is expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("a.b.c", False),
        ("room.message.danmaku", False),
        ("room.*", True),
        ("a.*.b", True),
        ("tool.result.#", True),
        ("#", True),
    ],
)
def test_is_wildcard_pattern(pattern: str, expected: bool) -> None:
    assert EventBus._is_wildcard_pattern(pattern) is expected
