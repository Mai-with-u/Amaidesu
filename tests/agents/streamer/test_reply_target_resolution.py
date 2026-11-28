"""从本批弹幕反查"本次回复的观众"：reply_to 精确优先，其次 target 的消息 ID / 文本，最后兜底最后一条"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.decision_executor import (
    find_reply_target_message,
    resolve_reply_message_id,
    resolve_reply_quote,
    resolve_reply_target_user,
)
from src.agents.streamer.planner import RoundOutcome
from src.modules.events.payloads.room import GiftInfo, GuardInfo, RoomMessagePayload, RoomMessageUser, SuperChatInfo
from src.modules.subtitle.protocol import ReplyQuote


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


# 回复目标定位与引用弹幕（昵称 + 原文）：与 message_id 同源定位


def test_find_reply_target_message_returns_the_batch_message() -> None:
    assert find_reply_target_message(RoundOutcome(target="m1"), _BATCH) is _BATCH[0]
    assert find_reply_target_message(RoundOutcome(reply_to="[id:m2]"), _BATCH) is _BATCH[1]
    assert find_reply_target_message(RoundOutcome(reply_to="user_bob: 你好呀主播"), _BATCH) is _BATCH[1]
    assert find_reply_target_message(RoundOutcome(target="不存在的id"), _BATCH) is None
    assert find_reply_target_message(RoundOutcome(target=None), _BATCH) is None


@pytest.mark.parametrize(
    ("plan", "batch", "expected"),
    [
        (RoundOutcome(target="m1"), _BATCH, ReplyQuote(user_name="user_alice", text="今天玩什么？")),
        (RoundOutcome(reply_to="[id:m3"), _BATCH, ReplyQuote(user_name="user_carol", text="ccc")),
        (RoundOutcome(reply_to="user_bob: 你好呀主播"), _BATCH, ReplyQuote(user_name="user_bob", text="你好呀主播")),
        (RoundOutcome(reply_to="该死的断触: 第二条"), _BATCH_DUP, None),  # 该用户本轮两条，宁缺毋假
        (RoundOutcome(target="不存在的id"), _BATCH, None),  # 解析不中不显示引用行
        (RoundOutcome(target=None), _BATCH, None),
        (RoundOutcome(target="m1"), [], None),
    ],
)
def test_resolve_reply_quote(plan: RoundOutcome, batch: list, expected: Optional[ReplyQuote]) -> None:
    assert resolve_reply_quote(plan, batch) == expected


_PAID_BATCH: List[RoomMessagePayload] = [
    RoomMessagePayload(
        message_type="gift",
        user=RoomMessageUser(id="u_a", name="user_a"),
        content="",
        message_id="msg_gift_1",
        gift=GiftInfo(name="小星星", count=1, total_price=1000, currency="bilibili_gold_coin"),
    ),
    RoomMessagePayload(
        message_type="super_chat",
        user=RoomMessageUser(id="u_b", name="user_b"),
        content="主播加油",
        message_id="9527",
        sc=SuperChatInfo(total_price=30000, currency="bilibili_gold_coin", message_id="9527"),
    ),
    RoomMessagePayload(
        message_type="guard",
        user=RoomMessageUser(id="u_c", name="user_c"),
        content="user_c 开通了舰长",
        message_id="msg_guard_1",
        guard=GuardInfo(guard_level=3, guard_num=1, guard_unit="月", total_price=138000),
    ),
    RoomMessagePayload(
        message_type="gift",
        user=RoomMessageUser(id="u_d", name="昵称甲（原名）"),
        content="",
        message_id="msg_bracket_1",
        gift=GiftInfo(name="小星星", count=1, total_price=1000, currency="bilibili_gold_coin"),
    ),
    RoomMessagePayload(
        message_type="gift",
        user=RoomMessageUser(id="u_dup", name="user_dup"),
        content="",
        message_id="msg_dup_1",
    ),
    RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u_dup", name="user_dup"),
        content="顺便发条弹幕",
        message_id="msg_dup_2",
    ),
]


def test_canonical_renders_paid_messages_with_id_suffix() -> None:
    """顶层 message_id 补齐后，canonical 渲染礼物/SC/上舰均在行尾携带 [id:...] 供 planner 引用。"""
    gift_line = canonical.batch_item_to_message(_PAID_BATCH[0], paid=True)["content"]
    sc_line = canonical.batch_item_to_message(_PAID_BATCH[1], paid=True)["content"]
    guard_line = canonical.batch_item_to_message(_PAID_BATCH[2], paid=True)["content"]
    assert gift_line.startswith("[礼物] user_a（付费观众）:") and gift_line.endswith("[id:msg_gift_1]")
    assert sc_line.startswith("[醒目留言] user_b（付费观众）:") and sc_line.endswith("[id:9527]")
    assert guard_line.startswith("[上舰] user_c（付费观众）:") and guard_line.endswith("[id:msg_guard_1]")


@pytest.mark.parametrize(
    ("target", "expected_id"),
    [
        ("[id:msg_gift_1]", "msg_gift_1"),
        ("[礼物] user_a（付费观众）: 送出 小星星 ×1", "msg_gift_1"),
        ("[礼物] user_a(付费观众): 送出 小星星 ×1", "msg_gift_1"),
        ("[SC] user_b（付费观众）: 主播加油", "9527"),
        ("[醒目留言] user_b（付费观众）: 主播加油", "9527"),
        ("[上舰] user_c（付费观众）: user_c 开通了舰长", "msg_guard_1"),
        ("昵称甲（原名）: 我名字里本来就有括号", "msg_bracket_1"),
        ("[礼物] 昵称甲（原名）（付费观众）: 送出 小星星 ×1", "msg_bracket_1"),
        ("[礼物] user_dup（付费观众）: 送出礼物", None),  # 同用户本轮两条消息，宁缺毋假
        ("[礼物] 不存在的观众（付费观众）: 送出礼物", None),
    ],
    ids=[
        "gift-by-id",
        "gift-prefix-fullwidth-title",
        "gift-prefix-ascii-title",
        "sc-short-prefix",
        "sc-canonical-prefix",
        "guard-prefix-title",
        "real-name-with-brackets",
        "real-name-with-brackets-plus-paid-title",
        "duplicate-user-returns-none",
        "unknown-user-returns-none",
    ],
)
def test_find_reply_target_message_handles_paid_prefixes_and_titles(
    target: str,
    expected_id: Optional[str],
) -> None:
    """planner 传回带 [礼物]/[SC]/[醒目留言]/[上舰] 前缀与头衔括号的展示串时能精确唯一反查，重名仍宁缺毋假。"""
    found = find_reply_target_message(RoundOutcome(target=target), _PAID_BATCH)
    assert (found.message_id if found is not None else None) == expected_id
