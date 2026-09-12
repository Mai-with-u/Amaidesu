"""存储域测试辅助：构造事件载荷的便利工厂。"""

from __future__ import annotations

from typing import Optional

from src.modules.events.payloads.room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.time_utils import now_ms


def make_room_message(
    *,
    message_type: str = "danmaku",
    live_session_id: int = 0,
    message_id: str = "",
    user: Optional[RoomMessageUser] = None,
    content: str = "",
    gift_name: str = "小星星",
    gift_count: int = 1,
    sc_amount: float = 50.0,
    simulated: bool = False,
    timestamp_ms: Optional[int] = None,
) -> RoomMessagePayload:
    """构造 ``RoomMessagePayload``：避免测试内重复样板。

    ``live_session_id`` 默认 0（未归属），由场次盖章拦截器或测试内显式指定。
    """
    if user is None:
        user = RoomMessageUser(id="tester", name="测试观众")

    common: dict = {
        "live_session_id": live_session_id,
        "message_id": message_id,
        "message_type": message_type,  # type: ignore[arg-type]
        "user": user,
        "content": content,
        "simulated": simulated,
    }
    if timestamp_ms is not None:
        common["timestamp_ms"] = timestamp_ms
    else:
        common["timestamp_ms"] = now_ms()

    if message_type == "gift":
        common["gift"] = GiftInfo(name=gift_name, count=gift_count)
    elif message_type == "super_chat":
        common["sc"] = SuperChatInfo(amount=sc_amount)

    return RoomMessagePayload(**common)


__all__ = ["make_room_message"]
