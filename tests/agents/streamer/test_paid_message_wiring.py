"""付费消息接线：SC / 礼物 / 上舰经事件订阅进缓冲并按类型强制，立即以 forced 决策；参考段标注优先回应"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent


def _message(message_type: str, content: str = "感谢主播") -> RoomMessagePayload:
    user = RoomMessageUser(id="u1", name="测试观众")
    return RoomMessagePayload(message_type=message_type, user=user, content=content, timestamp_ms=now_ms())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("message_type", "event_name"),
    [
        ("gift", CoreEvents.ROOM_MESSAGE_GIFT),
        ("super_chat", CoreEvents.ROOM_MESSAGE_SUPER_CHAT),
        ("guard", CoreEvents.ROOM_MESSAGE_GUARD),
    ],
)
async def test_paid_event_is_buffered_forced_and_flushed_immediately(message_type: str, event_name: str) -> None:
    """强制由消息类型决定（不看 importance），付费点名立即进决策轮。"""
    bus = EventBus()
    agent = make_streamer_agent(event_bus=bus)
    agent._subscribe_events()
    plan = AsyncMock(return_value=None)
    agent._rounds._planner.plan = plan
    try:
        await bus.emit(event_name, _message(message_type), source="test")
        await asyncio.sleep(0.05)
        assert agent._buffer.size == 1 and agent._buffer.force is True

        await agent._maybe_flush()
        plan.assert_awaited_once()
        assert plan.await_args.kwargs.get("forced") is True
    finally:
        await bus.cleanup()


async def test_normal_danmaku_is_not_forced_nor_flushed_early() -> None:
    agent = make_streamer_agent()
    plan = AsyncMock(return_value=None)
    agent._rounds._planner.plan = plan

    await agent.handle_message(_message("danmaku", "哈哈"))
    assert agent._buffer.force is False
    await agent._maybe_flush()
    plan.assert_not_awaited()


async def test_planner_reference_marks_priority_only_for_forced_batch() -> None:
    planner = Planner(
        config={},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        # 替身声明从没开过口：参考段会读上次开口时刻决定要不要写冷场行
        room_state=MagicMock(last_speech_ms=None),
        context_enabled=False,
    )
    forced_ref, _ = await planner._assemble_reference([], None, None, True, False, "")
    plain_ref, _ = await planner._assemble_reference([], None, None, False, False, "")
    assert "优先回应" in forced_ref
    assert "优先回应" not in plain_ref
