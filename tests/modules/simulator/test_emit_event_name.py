"""模拟器事件名选择测试：_emit_message / _emit_replay_payload 按 message_type 选事件名。

此前两处 emit 恒发 room.message.danmaku，导致 gift/super_chat/guard 类型的
模拟消息落错事件频道；本文件锁定"按 message_type 映射 room.message.* 常量、
未知类型回退弹幕且不抛错"的契约。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.simulator import SimulatorService


def _make_service() -> SimulatorService:
    """构造带 mock EventBus 的服务实例（不启动主循环，直接调 emit 方法）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()
    return SimulatorService(event_bus=event_bus)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "expected_event"),
    [
        ("danmaku", CoreEvents.ROOM_MESSAGE_DANMAKU),
        ("gift", CoreEvents.ROOM_MESSAGE_GIFT),
        ("super_chat", CoreEvents.ROOM_MESSAGE_SUPER_CHAT),
        ("guard", CoreEvents.ROOM_MESSAGE_GUARD),
    ],
)
async def test_emit_message_selects_event_by_type(message_type: str, expected_event: str) -> None:
    """_emit_message 按 message_type 发对应 room.message.* 事件。"""
    service = _make_service()
    persona = MagicMock(user_id="u1", user_nickname="观众甲")

    await service._emit_message(message_type=message_type, text="测试内容", persona=persona)

    service.event_bus.emit.assert_awaited_once()
    args, kwargs = service.event_bus.emit.await_args
    assert args[0] == expected_event
    assert args[0] != CoreEvents.ROOM_MESSAGE_DANMAKU or message_type == "danmaku"
    assert kwargs["source"] == "simulated_live_stream"
    payload = args[1]
    assert payload.message_type == message_type
    assert payload.simulated is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_type", "expected_event"),
    [
        ("gift", CoreEvents.ROOM_MESSAGE_GIFT),
        ("super_chat", CoreEvents.ROOM_MESSAGE_SUPER_CHAT),
        ("guard", CoreEvents.ROOM_MESSAGE_GUARD),
        ("danmaku", CoreEvents.ROOM_MESSAGE_DANMAKU),
    ],
)
async def test_emit_replay_payload_selects_event_by_type(message_type: str, expected_event: str) -> None:
    """_emit_replay_payload 同样按 payload.message_type 选事件名。"""
    service = _make_service()
    payload = RoomMessagePayload(
        message_id="replay-1",
        message_type=message_type,
        user={"id": "u1", "name": "观众甲"},
        content="回放内容",
        timestamp_ms=1_000,
        simulated=True,
    )

    await service._emit_replay_payload(payload)

    service.event_bus.emit.assert_awaited_once()
    args, _ = service.event_bus.emit.await_args
    assert args[0] == expected_event
    assert args[1].message_type == message_type


@pytest.mark.asyncio
async def test_emit_message_unknown_type_falls_back_to_danmaku() -> None:
    """未知 message_type 回退弹幕事件名，且不抛错。"""
    service = _make_service()
    persona = MagicMock(user_id="u1", user_nickname="观众甲")

    await service._emit_message(message_type="mystery", text="异常类型", persona=persona)

    args, _ = service.event_bus.emit.await_args
    assert args[0] == CoreEvents.ROOM_MESSAGE_DANMAKU
    # payload 的 message_type 同步钳制为 danmaku（Literal 校验不接受未知值）
    assert args[1].message_type == "danmaku"
