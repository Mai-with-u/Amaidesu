"""EventBroadcaster：start 订阅哪些事件、各事件转成什么 WS type 与载荷、stop 退订全部"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.modules.dashboard.websocket.broadcaster import EventBroadcaster
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    GamePayload,
    LLMContextUsedPayload,
    RoomMessagePayload,
    RoomMessageUser,
    RundownChangedPayload,
    StreamerSpeechPayload,
    TaskChangedPayload,
    ToolResultPayload,
)
from src.modules.events.payloads.tool_health import ToolHealthPayload


class _FakeEventBus:
    def __init__(self) -> None:
        self.subscribed: dict[str, Any] = {}
        self.unsubscribed: list[tuple[str, Any]] = []

    def on(self, event_name: str, handler, model_class) -> None:
        self.subscribed[event_name] = (handler, model_class)

    def off(self, event_name: str, handler) -> None:
        self.unsubscribed.append((event_name, handler))
        self.subscribed.pop(event_name, None)


class _FakeWS:
    def __init__(self) -> None:
        self.broadcast = AsyncMock(return_value=1)


@pytest.fixture
async def started():
    """(bus, ws, deliver)：deliver(event, payload) 走已订阅的 handler，返回最近一次广播调用。"""
    bus, ws = _FakeEventBus(), _FakeWS()
    broadcaster = EventBroadcaster(event_bus=bus, ws_handler=ws)
    await broadcaster.start()
    bus.broadcaster = broadcaster  # type: ignore[attr-defined]

    async def deliver(event_name: str, payload: Any, subscription: str | None = None):
        handler, _model = bus.subscribed[subscription or event_name]
        await handler(event_name, payload, source="test")
        return ws.broadcast.await_args

    yield bus, ws, deliver
    await broadcaster.stop()


def _room(message_type: str) -> RoomMessagePayload:
    return RoomMessagePayload(message_type=message_type, user=RoomMessageUser(id="u1", name="测试"), content="")  # type: ignore[arg-type]


async def test_start_subscribes_to_every_forwarded_event_and_stop_unsubscribes_all(started) -> None:
    bus, _ws, _deliver = started
    expected = {
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        CoreEvents.ROOM_MESSAGE_GIFT,
        CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        CoreEvents.ROOM_MESSAGE_ENTER,
        CoreEvents.RUNDOWN_CHANGED,
        CoreEvents.TOOL_RESULT_WILDCARD,
        CoreEvents.TOOL_HEALTH_WILDCARD,
        CoreEvents.STREAMER_SPEECH,
        CoreEvents.GAME_REPORT,
        CoreEvents.GAME_ATTENTION_REQUIRED,
        CoreEvents.GAME_ERROR,
        CoreEvents.GAME_MILESTONE,
        CoreEvents.TASK_CHANGED,
        CoreEvents.LLM_CONTEXT_USED,
    }
    assert expected <= set(bus.subscribed)
    assert bus.subscribed[CoreEvents.TASK_CHANGED][1] is TaskChangedPayload
    assert bus.subscribed[CoreEvents.LLM_CONTEXT_USED][1] is LLMContextUsedPayload

    subscribed_count = len(bus.subscribed)
    await bus.broadcaster.stop()  # type: ignore[attr-defined]
    assert len(bus.unsubscribed) == subscribed_count and bus.subscribed == {}


@pytest.mark.parametrize(
    "event_name", [CoreEvents.ROOM_MESSAGE_GIFT, CoreEvents.ROOM_MESSAGE_SUPER_CHAT, CoreEvents.ROOM_MESSAGE_ENTER]
)
async def test_room_message_events_share_one_ws_type(started, event_name: str) -> None:
    _bus, _ws, deliver = started
    payload = _room(event_name.rsplit(".", 1)[-1])
    call = await deliver(event_name, payload)
    assert call.args[0] == "room.message" and isinstance(call.args[1], dict)
    assert call.kwargs.get("message_id") == payload.id


async def test_rundown_changed_and_streamer_speech_payloads(started) -> None:
    _bus, _ws, deliver = started
    rundown = RundownChangedPayload(
        rundown_id="rd_test", segment_id="self_intro", segment_title="自我介绍", index=1, total=4, by="human"
    )
    call = await deliver(CoreEvents.RUNDOWN_CHANGED, rundown)
    assert call.args[0] == "rundown.changed"
    assert (call.args[1]["segment_id"], call.args[1]["by"]) == ("self_intro", "human")

    speech = StreamerSpeechPayload(
        utterance_id="utt_1700000000000_1", text="欢迎来到直播间！", emotion="happy", emotion_intensity=0.5
    )
    call = await deliver(CoreEvents.STREAMER_SPEECH, speech)
    assert call.args[0] == "streamer.speech" and call.kwargs.get("message_id") == speech.id
    assert (call.args[1]["utterance_id"], call.args[1]["text"], call.args[1]["emotion"]) == (
        "utt_1700000000000_1",
        "欢迎来到直播间！",
        "happy",
    )


async def test_wildcard_subscriptions_keep_concrete_event_name_as_ws_type(started) -> None:
    """tool.result.# / tool.health.# 通配订阅收到具体事件名后，WS type 沿用该具体名而不是通配串。"""
    _bus, _ws, deliver = started
    result = ToolResultPayload(tool_name="speak", status="success", result={"speech_text": "你好"})
    call = await deliver("tool.result.speak", result, CoreEvents.TOOL_RESULT_WILDCARD)
    assert call.args[0] == "tool.result.speak"
    assert (call.args[1]["tool_name"], call.args[1]["status"]) == ("speak", "success")

    health = ToolHealthPayload(
        tool_name="maicraft_speak",
        provider="maicraft",
        state="open",
        failure_count=3,
        last_error="ConnectionError: MCP server 未连接且重连失败",
        timestamp_ms=1706745600000,
    )
    call = await deliver("tool.health.maicraft_speak", health, CoreEvents.TOOL_HEALTH_WILDCARD)
    assert call.args[0] == "tool.health.maicraft_speak" and call.kwargs.get("message_id") == health.id
    for field in ("tool_name", "provider", "state", "failure_count", "last_error", "timestamp_ms"):
        assert call.args[1][field] == getattr(health, field)


@pytest.mark.parametrize(
    ("event_name", "event_type", "extra"),
    [
        (CoreEvents.GAME_REPORT, "report", {"report_kind": "delivery"}),
        (CoreEvents.GAME_ATTENTION_REQUIRED, "attention_required", {}),
        (CoreEvents.GAME_ERROR, "error", {}),
        (CoreEvents.GAME_MILESTONE, "milestone", {}),
    ],
)
async def test_game_events_pass_through_with_concrete_name(started, event_name, event_type, extra) -> None:
    _bus, _ws, deliver = started
    payload = GamePayload(game="minecraft", event_type=event_type, message="交付总结", **extra)  # type: ignore[arg-type]
    call = await deliver(event_name, payload)
    assert call.args[0] == event_name and call.kwargs.get("message_id") == payload.id
    assert (call.args[1]["event_type"], call.args[1]["message"]) == (event_type, "交付总结")


async def test_task_and_context_events_pass_through(started) -> None:
    _bus, _ws, deliver = started
    task = TaskChangedPayload(
        task_id="deleg_1", status="running", initiator="operator", executor="minecraft", summary="执行 Agent 已开始处理"
    )
    assert (await deliver(CoreEvents.TASK_CHANGED, task)).args[0] == CoreEvents.TASK_CHANGED

    sections = [
        {"key": "system", "tokens": 100, "raw_tokens": 90, "count": 1, "items": []},
        {"key": "messages", "tokens": 700, "raw_tokens": 630, "count": 8, "items": []},
        {"key": "tools", "tokens": 200, "raw_tokens": 180, "count": 2, "items": []},
    ]
    usage = LLMContextUsedPayload(
        request_id="req_cb", profile_name="replyer", model_name="glm-4.7", context_window=128_000,
        api_prompt_tokens=1_000, completion_tokens=200, sections=sections, calibrated=True,
    )  # fmt: skip
    call = await deliver(CoreEvents.LLM_CONTEXT_USED, usage)
    assert call.args[0] == "llm.context.used" and call.kwargs.get("message_id") == usage.id
    assert (call.args[1]["request_id"], call.args[1]["api_prompt_tokens"], len(call.args[1]["sections"])) == (
        "req_cb",
        1_000,
        3,
    )
