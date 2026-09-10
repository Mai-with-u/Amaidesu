"""EventBroadcaster 订阅测试（Wave U1 / B5）

覆盖：
1. 订阅 CoreEvents.ROOM_MESSAGE_{GIFT,SUPER_CHAT,ENTER} 并以 "room.message"
   WS type 转发（与已有 danmaku 一致）
2. 订阅 CoreEvents.RUNDOWN_CHANGED 并以 "rundown.changed" WS type 转发
3. 订阅 CoreEvents.TOOL_RESULT_WILDCARD 通配模式，WS type 沿用具体事件名
4. 订阅 CoreEvents.TOOL_HEALTH_WILDCARD 通配模式，WS type 沿用具体事件名
5. 取消订阅（stop）正常
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    RoomMessagePayload,
    RoomMessageUser,
    RundownChangedPayload,
    StreamerSpeechPayload,
    ToolResultPayload,
)
from src.modules.events.payloads.tool_health import ToolHealthPayload


class _FakeWSHandler:
    def __init__(self) -> None:
        self.broadcast = AsyncMock(return_value=1)


class _FakeEventBus:
    def __init__(self) -> None:
        self.subscribed: dict[str, Any] = {}
        self.unsubscribed: list[tuple[str, Any]] = []

    def on(self, event_name: str, handler, model_class) -> None:
        self.subscribed[event_name] = (handler, model_class)

    def off(self, event_name: str, handler) -> None:
        self.unsubscribed.append((event_name, handler))
        self.subscribed.pop(event_name, None)


@pytest.fixture
def bus_and_handler():
    bus = _FakeEventBus()
    ws = _FakeWSHandler()
    return bus, ws


@pytest.mark.asyncio
async def test_broadcaster_subscribes_to_4_new_events(bus_and_handler) -> None:
    """Wave U1 / B5：4 项新增订阅。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    for event_name in (
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        CoreEvents.ROOM_MESSAGE_GIFT,
        CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        CoreEvents.ROOM_MESSAGE_ENTER,
        CoreEvents.RUNDOWN_CHANGED,
        CoreEvents.TOOL_RESULT_WILDCARD,
    ):
        assert event_name in bus.subscribed, f"未订阅 {event_name}"

    await broadcaster.stop()


@pytest.mark.asyncio
async def test_room_message_events_broadcast_as_room_message_type(bus_and_handler) -> None:
    """room.message.{danmaku,gift,super_chat,enter} 一致映射到 ws type 'room.message'。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    for source_event in (
        CoreEvents.ROOM_MESSAGE_GIFT,
        CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        CoreEvents.ROOM_MESSAGE_ENTER,
    ):
        payload = RoomMessagePayload(
            message_type=(
                "gift"
                if source_event.endswith("gift")
                else "super_chat"
                if source_event.endswith("super_chat")
                else "enter"
            ),
            user=RoomMessageUser(id="u1", name="测试"),
            content="",
        )
        handler, _model_cls = bus.subscribed[source_event]
        await handler(source_event, payload, source="test")

        ws.broadcast.assert_awaited()
        call = ws.broadcast.await_args
        assert call.args[0] == "room.message", f"event {source_event} 应映射到 'room.message'"
        assert isinstance(call.args[1], dict)
        assert call.kwargs.get("message_id") == payload.id


@pytest.mark.asyncio
async def test_rundown_changed_broadcast_as_rundown_changed_type(bus_and_handler) -> None:
    """rundown.changed → ws type 'rundown.changed'。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    payload = RundownChangedPayload(
        rundown_id="rd_test",
        segment_id="self_intro",
        segment_title="自我介绍",
        index=1,
        total=4,
        by="human",
    )
    handler, _model_cls = bus.subscribed[CoreEvents.RUNDOWN_CHANGED]
    await handler(CoreEvents.RUNDOWN_CHANGED, payload, source="test")

    ws.broadcast.assert_awaited_once()
    call = ws.broadcast.await_args
    assert call.args[0] == "rundown.changed"
    assert isinstance(call.args[1], dict)
    assert call.args[1]["segment_id"] == "self_intro"
    assert call.args[1]["by"] == "human"


@pytest.mark.asyncio
async def test_tool_result_wildcard_preserves_concrete_event_name(bus_and_handler) -> None:
    """tool.result.# 通配订阅收到具体事件名（tool.result.speak）后，
    WS type 沿用该具体名（不是通配串）。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    payload = ToolResultPayload(
        tool_name="speak",
        status="success",
        result={"speech_text": "你好", "audio_duration_ms": 3200},
    )
    handler, _model_cls = bus.subscribed[CoreEvents.TOOL_RESULT_WILDCARD]
    concrete_event = "tool.result.speak"
    await handler(concrete_event, payload, source="test")

    ws.broadcast.assert_awaited_once()
    call = ws.broadcast.await_args
    # ws type 沿用具体事件名本身（不是 "tool.result.#" 通配串）
    assert call.args[0] == "tool.result.speak"
    assert call.args[1]["tool_name"] == "speak"
    assert call.args[1]["status"] == "success"


@pytest.mark.asyncio
async def test_tool_health_wildcard_preserves_concrete_event_name(bus_and_handler) -> None:
    """tool.health.# 通配订阅收到具体事件名（tool.health.maicraft_speak）后，
    WS type 沿用该具体名（不是通配串）；payload 字段透传。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    payload = ToolHealthPayload(
        tool_name="maicraft_speak",
        provider="maicraft",
        state="open",
        failure_count=3,
        last_error="ConnectionError: MCP server 未连接且重连失败",
        timestamp_ms=1706745600000,
    )
    handler, _model_cls = bus.subscribed[CoreEvents.TOOL_HEALTH_WILDCARD]
    concrete_event = "tool.health.maicraft_speak"
    await handler(concrete_event, payload, source="test")

    ws.broadcast.assert_awaited_once()
    call = ws.broadcast.await_args
    # ws type 沿用具体事件名本身（不是 "tool.health.#" 通配串）
    assert call.args[0] == "tool.health.maicraft_speak"
    assert call.args[1]["tool_name"] == "maicraft_speak"
    assert call.args[1]["provider"] == "maicraft"
    assert call.args[1]["state"] == "open"
    assert call.args[1]["failure_count"] == 3
    assert call.args[1]["last_error"] == "ConnectionError: MCP server 未连接且重连失败"
    assert call.args[1]["timestamp_ms"] == 1706745600000
    assert call.kwargs.get("message_id") == payload.id


@pytest.mark.asyncio
async def test_broadcaster_subscribes_to_tool_health_wildcard(bus_and_handler) -> None:
    """broadcaster.start() 应订阅 CoreEvents.TOOL_HEALTH_WILDCARD。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()
    assert CoreEvents.TOOL_HEALTH_WILDCARD in bus.subscribed
    await broadcaster.stop()


@pytest.mark.asyncio
async def test_streamer_speech_broadcast_as_streamer_speech_type(bus_and_handler) -> None:
    """streamer.speech → ws type 'streamer.speech'（payload 含 utterance_id/text/emotion）。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    payload = StreamerSpeechPayload(
        utterance_id="utt_1700000000000_1",
        text="欢迎来到直播间！",
        emotion="happy",
    )
    handler, _model_cls = bus.subscribed[CoreEvents.STREAMER_SPEECH]
    await handler(CoreEvents.STREAMER_SPEECH, payload, source="test")

    ws.broadcast.assert_awaited_once()
    call = ws.broadcast.await_args
    assert call.args[0] == "streamer.speech"
    assert isinstance(call.args[1], dict)
    assert call.args[1]["utterance_id"] == "utt_1700000000000_1"
    assert call.args[1]["text"] == "欢迎来到直播间！"
    assert call.args[1]["emotion"] == "happy"
    assert call.kwargs.get("message_id") == payload.id


@pytest.mark.asyncio
async def test_broadcaster_unsubscribes_all_events_on_stop(bus_and_handler) -> None:
    """stop() 调用 event_bus.off() 清理全部订阅。"""
    from src.modules.dashboard.websocket.broadcaster import EventBroadcaster

    bus, ws = bus_and_handler
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()

    subscribed_count = len(bus.subscribed)
    assert subscribed_count > 0

    await broadcaster.stop()
    assert len(bus.unsubscribed) == subscribed_count
    assert len(bus.subscribed) == 0
