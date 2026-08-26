"""DanmakuWidgetService 测试（v2 语义域事件直转路径）。

覆盖回归：2026-08-26 运行时崩溃
- '_DanmakuWidgetService' object has no attribute '_convert_payload_to_widget'
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.modules.dashboard.widget.models import MessageType
from src.modules.dashboard.widget.service import DanmakuWidgetService
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)


def _make_payload(**overrides: Any) -> RoomMessagePayload:
    kwargs: dict[str, Any] = dict(
        live_session_id="ls_test",
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众A"),
        content="主播好可爱",
        timestamp_ms=1_706_745_600_000,
    )
    kwargs.update(overrides)
    return RoomMessagePayload(**kwargs)


def _make_service() -> DanmakuWidgetService:
    return DanmakuWidgetService(event_bus=EventBus())


def test_convert_danmaku_message() -> None:
    svc = _make_service()
    msg = svc._convert_payload_to_widget(_make_payload())
    assert msg is not None
    assert msg.message_type == MessageType.TEXT
    assert msg.user_name == "观众A"
    assert msg.user_id == "u1"
    assert msg.content == "主播好可爱"
    assert msg.room_id == "ls_test"


def test_convert_gift_message() -> None:
    svc = _make_service()
    msg = svc._convert_payload_to_widget(
        _make_payload(
            message_type="gift",
            content="",
            gift=GiftInfo(name="小星星", count=5),
        )
    )
    assert msg is not None
    assert msg.message_type == MessageType.GIFT
    assert msg.gift_name == "小星星"
    assert msg.gift_count == 5


def test_convert_super_chat_message() -> None:
    svc = _make_service()
    msg = svc._convert_payload_to_widget(
        _make_payload(
            message_type="super_chat",
            content="SC 内容",
            sc=SuperChatInfo(amount=50.0),
        )
    )
    assert msg is not None
    assert msg.message_type == MessageType.SUPER_CHAT
    assert msg.sc_price == 50.0
    assert msg.sc_message == "SC 内容"


def test_convert_enter_message() -> None:
    svc = _make_service()
    msg = svc._convert_payload_to_widget(_make_payload(message_type="enter", content=""))
    assert msg is not None
    assert msg.message_type == MessageType.ENTER


@pytest.mark.asyncio
async def test_on_input_message_roundtrip() -> None:
    """端到端：事件回调 → 转换 → 入队 → 广播（回归无 AttributeError/NoneType）"""
    svc = _make_service()
    svc._broadcast_callback = AsyncMock()
    svc._danmaku_callback = AsyncMock()
    await svc._on_input_message("room.message.danmaku", _make_payload(), "console")
    assert len(svc.messages) == 1
    svc._danmaku_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_input_message_missing_user_fallback() -> None:
    svc = _make_service()
    svc._broadcast_callback = AsyncMock()
    await svc._on_input_message("room.message.danmaku", _make_payload(), "console")
    assert svc.messages[0].user_name == "观众A"
