"""从本批弹幕反查"本次回复的观众"：reply_to 精确优先，其次 target 的消息 ID / 文本，最后兜底最后一条"""

from __future__ import annotations

from typing import Optional

import pytest

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from tests.agents.streamer.helpers import make_streamer_agent


def _msg(text: str, user_id: str, message_id: str) -> RoomMessagePayload:
    user = RoomMessageUser(id=user_id, name=f"user_{user_id}")
    return RoomMessagePayload(message_type="danmaku", user=user, content=text, message_id=message_id)


_BATCH = [_msg("今天玩什么？", "alice", "m1"), _msg("你好呀主播", "bob", "m2"), _msg("ccc", "carol", "m3")]


@pytest.mark.parametrize(
    ("plan", "batch", "expected"),
    [
        ({"target": "m1"}, _BATCH, "alice"),  # 命中消息 ID
        ({"target": "你好呀"}, _BATCH, "bob"),  # 文本子串兜底
        ({"target": "观众A", "reply_to": "m2"}, _BATCH, "bob"),  # reply_to 优先于 target
        ({"reply_to": "不存在的id"}, _BATCH, None),  # reply_to 未命中不做文本兜底，防误关联
        ({"target": "zzz_unmatched"}, _BATCH, "carol"),  # 全未命中：保守兜底最后一条
        ({"target": None}, _BATCH, None),  # 主动发言/无特定目标
        ({"target": "m1"}, [], None),
    ],
)
def test_resolve_reply_target_user(plan: dict, batch: list, expected: Optional[str]) -> None:
    agent = make_streamer_agent()
    assert agent._rounds._resolve_reply_target_user(plan, batch) == expected
