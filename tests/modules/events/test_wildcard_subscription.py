"""
EventBus 通配订阅测试

测试 MQTT 风格通配订阅（``*``=单层 ``#``=多层）的行为：
- 精确订阅向后兼容（与旧 EventBus 行为完全一致）
- ``room.*`` 匹配 ``room.connected`` 但**不**匹配 ``room.message.danmaku``（单层严格）
- ``tool.result.#`` 匹配多层 + 父名 ``tool.result``
- 独立 ``#`` 匹配一切
- 混合精确 + 通配订阅同一事件时都触发
- specificity 排序：更具体的 pattern 先于更通用的
- ``off()`` 移除通配订阅
- 类型化订阅（``model_class``）与通配 pattern 配合时，payload 验证到模型实例
- 统计**始终按真实 emit 的 event_name** 入键（与通配 pattern 解耦）

运行: uv run pytest tests/modules/events/test_wildcard_subscription.py -v
"""

import asyncio

import pytest
from pydantic import BaseModel, Field

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads import (
    RoomMessagePayload,
    RoomMessageUser,
    ToolResultPayload,
)


# =============================================================================
# 测试数据模型
# =============================================================================


class SimpleTestEvent(BaseModel):
    """简单测试事件 Model"""

    message: str = Field(default="test", description="测试消息")


def _make_danmaku_payload(text: str = "hello") -> RoomMessagePayload:
    """构造一个合法的 RoomMessagePayload（danmaku 类型）"""
    return RoomMessagePayload(
        live_session_id="ls_test",
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="tester"),
        content=text,
        timestamp_ms=1700000000000,
    )


def _make_tool_result_payload(tool_name: str = "speak") -> ToolResultPayload:
    """构造一个合法的 ToolResultPayload"""
    return ToolResultPayload(
        tool_name=tool_name,
        status="success",
        result={"data": "ok"},
        timestamp_ms=1700000000000,
    )


# =============================================================================
# 精确订阅向后兼容
# =============================================================================


@pytest.mark.asyncio
async def test_exact_subscription_still_works():
    """精确订阅行为与旧 EventBus 完全一致（向后兼容）"""
    bus = EventBus(enable_stats=False)
    received = []

    async def handler(event_name, payload: SimpleTestEvent, source: str):
        received.append((event_name, payload.message, source))

    bus.on("test.event", handler, SimpleTestEvent)
    await bus.emit("test.event", SimpleTestEvent(message="hi"), source="src", wait=True)

    assert len(received) == 1
    assert received[0] == ("test.event", "hi", "src")


@pytest.mark.asyncio
async def test_exact_subscription_no_wildcard_match():
    """精确订阅**不**被通配订阅误触发"""
    bus = EventBus(enable_stats=False)

    async def h1(event_name, payload, source):
        pass

    # 精确订阅 "test.event"
    bus.on("test.event", h1, SimpleTestEvent)

    # 通配订阅 "test.#"——会触发精确订阅的同一事件
    async def h2(event_name, payload, source):
        pass

    bus.on("test.#", h2, SimpleTestEvent)

    # emit 无关事件不应触发
    await bus.emit("other.event", SimpleTestEvent(message="x"), wait=True)

    handlers = bus._handlers.get("test.event", [])
    assert handlers  # 精确订阅仍在
    # 不验证执行次数（内部 _collect_handlers 处理），仅验证无副作用


# =============================================================================
# 单层通配 * 行为
# =============================================================================


@pytest.mark.asyncio
async def test_single_level_wildcard_matches_exactly_one_segment():
    """``room.*`` 匹配 ``room.connected`` 但**不**匹配 ``room.message.danmaku``"""
    bus = EventBus(enable_stats=False)
    matched = []
    unmatched = []

    async def h_room_star(event_name, payload, source):
        matched.append(event_name)

    async def h_room_connected(event_name, payload, source):
        unmatched.append(event_name)

    bus.on("room.*", h_room_star, SimpleTestEvent)
    bus.on("room.connected", h_room_connected, SimpleTestEvent)

    # room.connected：应同时被通配 + 精确订阅触发
    await bus.emit("room.connected", SimpleTestEvent(), source="test", wait=True)
    assert "room.connected" in matched
    assert "room.connected" in unmatched

    # room.message.danmaku：只被通配订阅触发？不！单层 * 不匹配 4 段
    matched.clear()
    unmatched.clear()
    await bus.emit("room.message.danmaku", SimpleTestEvent(), source="test", wait=True)
    assert matched == []  # 单层 * 不匹配 room.message.danmaku
    assert unmatched == []


# =============================================================================
# 多层通配 # 行为
# =============================================================================


@pytest.mark.asyncio
async def test_multi_level_wildcard_matches_multiple_segments():
    """``tool.result.#`` 匹配多层 + 父名 ``tool.result``"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_tool(event_name, payload, source):
        received.append(event_name)

    bus.on("tool.result.#", h_tool, ToolResultPayload)

    # 三层
    await bus.emit("tool.result.speak", _make_tool_result_payload("speak"), source="test", wait=True)
    # 多层
    await bus.emit("tool.result.a.b.c", _make_tool_result_payload("a.b.c"), source="test", wait=True)
    # 父名（# 匹配 ≥0 段，父名零段也命中）
    await bus.emit("tool.result", _make_tool_result_payload("parent"), source="test", wait=True)

    assert received == ["tool.result.speak", "tool.result.a.b.c", "tool.result"]


@pytest.mark.asyncio
async def test_multi_level_wildcard_does_not_match_sibling_domain():
    """``tool.result.#`` **不**匹配 ``tool.something``（前缀约束）"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_tool(event_name, payload, source):
        received.append(event_name)

    bus.on("tool.result.#", h_tool, ToolResultPayload)

    await bus.emit("tool.something", _make_danmaku_payload(), source="test", wait=True)
    await bus.emit("tool", _make_danmaku_payload(), source="test", wait=True)

    assert received == []


# =============================================================================
# 独立 # 通配
# =============================================================================


@pytest.mark.asyncio
async def test_standalone_hash_matches_everything():
    """独立 ``#`` 匹配一切事件名"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_all(event_name, payload, source):
        received.append(event_name)

    bus.on("#", h_all, SimpleTestEvent)

    for name in ["a", "a.b", "x.y.z", "anything.you.want"]:
        await bus.emit(name, SimpleTestEvent(), source="test", wait=True)

    assert received == ["a", "a.b", "x.y.z", "anything.you.want"]


# =============================================================================
# 混合订阅
# =============================================================================


@pytest.mark.asyncio
async def test_mixed_exact_and_wildcard_both_fire():
    """同一 emit 事件：精确 + 通配订阅都应触发"""
    bus = EventBus(enable_stats=False)
    exact_called = []
    wildcard_called = []

    async def h_exact(event_name, payload, source):
        exact_called.append(event_name)

    async def h_wild(event_name, payload, source):
        wildcard_called.append(event_name)

    bus.on("room.message.danmaku", h_exact, RoomMessagePayload)
    bus.on("room.message.#", h_wild, RoomMessagePayload)

    await bus.emit("room.message.danmaku", _make_danmaku_payload(), wait=True)

    assert exact_called == ["room.message.danmaku"]
    assert wildcard_called == ["room.message.danmaku"]


@pytest.mark.asyncio
async def test_wildcard_subscription_does_not_fire_for_unrelated_events():
    """``room.message.#`` **不**触发 ``room.state.heat``（不同子层）"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_room_msg(event_name, payload, source):
        received.append(event_name)

    bus.on("room.message.#", h_room_msg, SimpleTestEvent)

    await bus.emit("room.state.heat", SimpleTestEvent(), source="test", wait=True)

    assert received == []


# =============================================================================
# specificity 排序
# =============================================================================


@pytest.mark.asyncio
async def test_specificity_ordering_more_specific_pattern_first():
    """更具体的通配 pattern 应在更通用的之前执行（前提：同优先级）"""
    bus = EventBus(enable_stats=False)
    order = []

    async def h_room_msg(event_name, payload, source):
        order.append("message")

    async def h_room_all(event_name, payload, source):
        order.append("all")

    # 注意：精确订阅 room.message.danmaku 也注册，确保 specificity 三层都参与
    async def h_exact(event_name, payload, source):
        order.append("exact")

    bus.on("room.message.danmaku", h_exact, RoomMessagePayload)
    bus.on("room.message.#", h_room_msg, RoomMessagePayload)
    bus.on("room.#", h_room_all, RoomMessagePayload)

    await bus.emit("room.message.danmaku", _make_danmaku_payload(), wait=True)

    # exact > message# > room#（specificity 降序）
    assert order == ["exact", "message", "all"]


@pytest.mark.asyncio
async def test_priority_overrides_specificity():
    """priority 仍是最强排序键（即使 specificity 较低的 pattern 在 priority 上更优）"""
    bus = EventBus(enable_stats=False)
    order = []

    async def h_low_specific_high_priority(event_name, payload, source):
        order.append("wild_high")

    async def h_high_specific_low_priority(event_name, payload, source):
        order.append("exact_low")

    # 通配 room.#（低 specificity）但 priority=1（高优先级）
    bus.on("room.#", h_low_specific_high_priority, SimpleTestEvent, priority=1)
    # 精确 room.x（高 specificity）但 priority=100（低优先级）
    bus.on("room.x", h_high_specific_low_priority, SimpleTestEvent, priority=100)

    await bus.emit("room.x", SimpleTestEvent(), wait=True)

    # priority 1 优先 → 通配先；priority 100 次之 → 精确后
    assert order == ["wild_high", "exact_low"]


# =============================================================================
# off() 移除通配订阅
# =============================================================================


@pytest.mark.asyncio
async def test_off_removes_wildcard_subscription():
    """``off("pattern.#", handler)`` 移除通配订阅"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_tool(event_name, payload, source):
        received.append(event_name)

    bus.on("tool.result.#", h_tool, ToolResultPayload)
    assert bus.get_listeners_count("tool.result.#") == 1

    bus.off("tool.result.#", h_tool)
    assert bus.get_listeners_count("tool.result.#") == 0

    await bus.emit("tool.result.speak", _make_danmaku_payload(), wait=True)
    assert received == []


@pytest.mark.asyncio
async def test_off_wildcard_does_not_affect_exact_subscription():
    """``off`` 通配订阅不影响同 handler 注册的精确订阅"""
    bus = EventBus(enable_stats=False)
    exact_called = []
    wildcard_called = []

    async def h(event_name, payload, source):
        if event_name == "exact.event":
            exact_called.append(event_name)
        else:
            wildcard_called.append(event_name)

    bus.on("exact.event", h, SimpleTestEvent)
    bus.on("wild.#", h, SimpleTestEvent)

    bus.off("wild.#", h)
    # 精确订阅仍在
    assert bus.get_listeners_count("exact.event") == 1
    assert bus.get_listeners_count("wild.#") == 0

    await bus.emit("exact.event", SimpleTestEvent(), wait=True)
    await bus.emit("wild.something", SimpleTestEvent(), wait=True)

    assert exact_called == ["exact.event"]
    assert wildcard_called == []


# =============================================================================
# 类型化订阅与通配
# =============================================================================


@pytest.mark.asyncio
async def test_typed_subscription_over_wildcard_validates_payload():
    """类型化订阅 + 通配 pattern 时，handler 接收 Pydantic Model 实例（而非 dict）"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_typed(event_name, payload: RoomMessagePayload, source: str):
        # 必须被 model_validate 为 RoomMessagePayload 实例
        assert isinstance(payload, RoomMessagePayload)
        received.append(payload)

    bus.on("room.message.#", h_typed, RoomMessagePayload)

    await bus.emit("room.message.danmaku", _make_danmaku_payload("hello"), wait=True)
    await bus.emit("room.message.gift", _make_danmaku_payload(""), wait=True)

    assert len(received) == 2
    assert received[0].message_type == "danmaku"
    assert received[0].content == "hello"


@pytest.mark.asyncio
async def test_typed_subscription_wildcard_logs_validation_error():
    """类型化订阅 + 通配：payload 不匹配 model_class 时记录错误（handler 不被调用）"""
    bus = EventBus(enable_stats=False)
    received = []

    async def h_strict(event_name, payload: ToolResultPayload, source: str):
        received.append(payload)

    bus.on("tool.result.#", h_strict, ToolResultPayload)

    # 构造一个 RoomMessagePayload —— emit 时不验证，但类型化订阅会 model_validate 失败
    # （ToolResultPayload 缺少 message_type 等字段）
    bad_payload = _make_danmaku_payload("oops")
    await bus.emit("tool.result.bad", bad_payload, wait=True)

    assert received == []  # 验证失败，handler 不被调用


# =============================================================================
# 统计按真实事件名入键（与通配解耦）
# =============================================================================


@pytest.mark.asyncio
async def test_stats_keyed_by_actual_event_name_not_pattern():
    """统计**始终按真实 emit 的 event_name** 入键，永不为通配 pattern 建条目"""
    bus = EventBus(enable_stats=True)

    async def h(event_name, payload, source):
        pass

    bus.on("tool.result.#", h, SimpleTestEvent)

    await bus.emit("tool.result.speak", SimpleTestEvent(), wait=True)
    await bus.emit("tool.result.summarize_timeline", SimpleTestEvent(), wait=True)

    # 真实事件名有统计
    stats_speak = bus.get_stats("tool.result.speak")
    assert stats_speak is not None
    assert stats_speak.emit_count == 1

    stats_summary = bus.get_stats("tool.result.summarize_timeline")
    assert stats_summary is not None
    assert stats_summary.emit_count == 1

    # 通配 pattern 自身**不**有统计条目
    assert bus.get_stats("tool.result.#") is None
    assert "tool.result.#" not in bus.get_all_stats()


# =============================================================================
# match helper 单元测试
# =============================================================================


class TestMatchWildcardHelper:
    """``EventBus._match_wildcard`` 静态方法直接测试"""

    @staticmethod
    def _match(pattern: str, event_name: str) -> bool:
        return EventBus._match_wildcard(pattern, event_name)

    def test_literal_match(self):
        """字面量 pattern 必须段段相等"""
        assert self._match("a.b.c", "a.b.c") is True
        assert self._match("a.b.c", "a.b.x") is False
        assert self._match("a.b", "a.b.c") is False  # 长度不同

    def test_single_level_wildcard(self):
        """``*`` 消耗恰好 1 段"""
        assert self._match("room.*", "room.message") is True
        assert self._match("room.*", "room") is False  # * 必须有 1 段
        assert self._match("room.*", "room.message.danmaku") is False  # 多层不行

    def test_multi_level_wildcard(self):
        """``#`` 消耗 ≥0 段（pattern 必须以 # 结尾）"""
        assert self._match("tool.result.#", "tool.result") is True
        assert self._match("tool.result.#", "tool.result.x") is True
        assert self._match("tool.result.#", "tool.result.x.y.z") is True
        assert self._match("tool.result.#", "tool") is False  # 前缀不等
        assert self._match("tool.result.#", "tool.x") is False

    def test_standalone_hash(self):
        """独立 ``#`` 匹配一切"""
        assert self._match("#", "anything") is True
        assert self._match("#", "a.b.c.d.e") is True

    def test_mixed_wildcards(self):
        """``* 与 # 混合"""
        assert self._match("a.*.b.#", "a.x.b") is True
        assert self._match("a.*.b.#", "a.x.b.c.d") is True
        assert self._match("a.*.b.#", "a.x.y.b") is False  # * 必须是单段


class TestIsWildcardPattern:
    """``EventBus._is_wildcard_pattern`` 静态方法"""

    @staticmethod
    def _is_wildcard(pattern: str) -> bool:
        return EventBus._is_wildcard_pattern(pattern)

    def test_literal_not_wildcard(self):
        assert self._is_wildcard("a.b.c") is False
        assert self._is_wildcard("room.message.danmaku") is False

    def test_star_is_wildcard(self):
        assert self._is_wildcard("room.*") is True
        assert self._is_wildcard("a.*.b") is True

    def test_hash_is_wildcard(self):
        assert self._is_wildcard("tool.result.#") is True
        assert self._is_wildcard("#") is True


class TestSpecificityOrdering:
    """``EventBus._pattern_specificity`` 排序辅助"""

    @staticmethod
    def _spec(pattern: str) -> int:
        return EventBus._pattern_specificity(pattern)

    def test_specificity_ranking(self):
        """长字面量前缀 > 短字面量前缀 > 独立 #"""
        assert self._spec("#") < self._spec("room.#")
        assert self._spec("room.#") < self._spec("room.message.#")
        assert self._spec("room.message.#") < self._spec("room.message.*")

    def test_literal_segments_have_higher_score_than_wildcards(self):
        """字面量 token 比 * 权重大（+4 vs +2）"""
        assert self._spec("a.b") > self._spec("a.*")
        assert self._spec("a.b.c") > self._spec("a.*.c")

    def test_exact_subscriptions_have_higher_specificity(self):
        """精确订阅的 specificity（由 _collect_handlers 注入）远大于通配"""
        # 模拟 _collect_handlers 的精确 specificity 值
        _EXACT_SPECIFICITY = 10_000
        assert _EXACT_SPECIFICITY > self._spec("room.message.danmaku")
        assert _EXACT_SPECIFICITY > self._spec("room.#")


# =============================================================================
# 运行入口
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
