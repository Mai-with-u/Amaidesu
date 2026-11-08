"""从本批弹幕反查"本次回复的观众"：reply_to 精确优先，其次 target 的消息 ID / 文本，最后兜底最后一条"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.agents.streamer.decision_executor import resolve_reply_message_id, resolve_reply_target_user
from src.agents.streamer.planner import RoundOutcome
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


def _msg(text: str, user_id: str, message_id: str) -> RoomMessagePayload:
    user = RoomMessageUser(id=user_id, name=f"user_{user_id}")
    return RoomMessagePayload(message_type="danmaku", user=user, content=text, message_id=message_id)


_BATCH: List[RoomMessagePayload] = [
    _msg("今天玩什么？", "alice", "m1"),
    _msg("你好呀主播", "bob", "m2"),
    _msg("ccc", "carol", "m3"),
]


@pytest.mark.parametrize(
    ("plan", "batch", "expected"),
    [
        (RoundOutcome(target="m1"), _BATCH, "alice"),  # 命中消息 ID
        (RoundOutcome(target="你好呀"), _BATCH, "bob"),  # 文本子串兜底
        (RoundOutcome(target="观众A", reply_to="m2"), _BATCH, "bob"),  # reply_to 优先于 target
        (RoundOutcome(reply_to="不存在的id"), _BATCH, None),  # reply_to 未命中不做文本兜底，防误关联
        (RoundOutcome(target="zzz_unmatched"), _BATCH, "carol"),  # 全未命中：保守兜底最后一条
        (RoundOutcome(target=None), _BATCH, None),  # 主动发言/无特定目标
        (RoundOutcome(target="m1"), [], None),
    ],
)
def test_resolve_reply_target_user(plan: RoundOutcome, batch: list, expected: Optional[str]) -> None:
    assert resolve_reply_target_user(plan, batch) == expected


def _msg_named(text: str, user_id: str, name: str, message_id: str) -> RoomMessagePayload:
    user = RoomMessageUser(id=user_id, name=name)
    return RoomMessagePayload(message_type="danmaku", user=user, content=text, message_id=message_id)


_BATCH_DUP: List[RoomMessagePayload] = [
    _msg_named("第一条", "dave", "该死的断触", "d1"),
    _msg_named("第二条", "dave", "该死的断触", "d2"),
    _msg_named("你好呀主播", "bob", "user_bob", "m2"),
]


@pytest.mark.parametrize(
    ("plan", "batch", "expected"),
    [
        (RoundOutcome(target="m1"), _BATCH, "m1"),  # target 恰为批内 message_id
        (RoundOutcome(reply_to="[id:m2]"), _BATCH, "m2"),  # [id:…] 引用写法
        (RoundOutcome(reply_to="[id:m3"), _BATCH, "m3"),  # 截断的引用写法
        (RoundOutcome(reply_to="user_bob: 你好呀主播"), _BATCH, "m2"),  # 用户名精确匹配且仅一条
        (RoundOutcome(reply_to="该死的断触: 你好呀主播"), _BATCH_DUP, None),  # 该用户本轮两条，宁缺毋假
        (RoundOutcome(target="不存在的id"), _BATCH, None),  # 解析不中不落假键
        (RoundOutcome(target=None), _BATCH, None),
        (RoundOutcome(target="m1"), [], None),
    ],
)
def test_resolve_reply_message_id(plan: RoundOutcome, batch: list, expected: Optional[str]) -> None:
    assert resolve_reply_message_id(plan, batch) == expected
