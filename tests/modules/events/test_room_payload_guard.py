"""RoomMessagePayload 上舰（guard）契约测试

覆盖：
- ``room.message.guard`` 已注册且映射 RoomMessagePayload
- message_type="guard" 合法构造；其他类型不被标为 guard（负向）
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.registry import get_registered_event


def test_guard_event_registered() -> None:
    """room.message.guard 已注册到事件注册表，Payload 为 RoomMessagePayload"""
    assert get_registered_event("room.message.guard") is RoomMessagePayload


def test_guard_message_type_constructs() -> None:
    """message_type="guard" 合法"""
    payload = RoomMessagePayload(
        message_type="guard",
        user=RoomMessageUser(id="u1", name="舰长君"),
        content="舰长君 开通了舰长",
    )
    assert payload.message_type == "guard"


def test_other_message_types_not_guard() -> None:
    """既有四类 message_type 值合法且不等于 guard"""
    for mt in ("danmaku", "gift", "super_chat", "enter"):
        payload = RoomMessagePayload(
            message_type=mt,
            user=RoomMessageUser(id="u1", name="用户"),
        )
        assert payload.message_type != "guard"


def test_unknown_message_type_rejected() -> None:
    """非法 message_type 被 Literal 拒绝"""
    with pytest.raises(ValidationError):
        RoomMessagePayload(
            message_type="guard_x",
            user=RoomMessageUser(id="u1", name="用户"),
        )
