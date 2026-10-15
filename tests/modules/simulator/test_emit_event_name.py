"""模拟器 emit：_emit_message / _emit_replay_payload 按 message_type 选 room.message.* 事件名，未知类型回退弹幕"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.simulator import SimulatorService

EVENT_BY_TYPE = {
    "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
    "gift": CoreEvents.ROOM_MESSAGE_GIFT,
    "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
    "guard": CoreEvents.ROOM_MESSAGE_GUARD,
}


def _service() -> SimulatorService:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return SimulatorService(event_bus=bus)  # 不启动主循环，直接调 emit 方法


@pytest.mark.parametrize(("message_type", "expected_event"), EVENT_BY_TYPE.items())
async def test_both_emit_paths_select_event_by_message_type(message_type: str, expected_event: str) -> None:
    service = _service()
    persona = MagicMock(user_id="u1", user_nickname="观众甲")
    await service._emit_message(message_type=message_type, text="测试内容", persona=persona)
    args, kwargs = service.event_bus.emit.await_args
    assert args[0] == expected_event and kwargs["source"] == "simulated_live_stream"
    assert (args[1].message_type, args[1].simulated) == (message_type, True)

    replay = RoomMessagePayload(
        message_id="replay-1",
        message_type=message_type,
        user={"id": "u1", "name": "观众甲"},
        content="回放内容",
        timestamp_ms=1_000,
        simulated=True,
    )
    await service._emit_replay_payload(replay)
    args, _ = service.event_bus.emit.await_args
    assert args[0] == expected_event and args[1].message_type == message_type


async def test_unknown_message_type_falls_back_to_danmaku_without_raising() -> None:
    service = _service()
    await service._emit_message(
        message_type="mystery", text="异常类型", persona=MagicMock(user_id="u1", user_nickname="甲")
    )
    args, _ = service.event_bus.emit.await_args
    assert (
        args[0] == CoreEvents.ROOM_MESSAGE_DANMAKU and args[1].message_type == "danmaku"
    )  # Literal 不接受未知值，同步钳制
