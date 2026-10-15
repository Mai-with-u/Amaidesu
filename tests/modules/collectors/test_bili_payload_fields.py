"""B 站采集器：payload 全字段映射（金额口径/身份快照/连击/盲盒/raw_data）、上舰与观看数事件形态、注册与元数据

喂真实形状的原始 dict → 断言 payload 全字段，兜住"采集层解析了但填错字段"的回归。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.bilibili import BiliDanmakuCollector, BiliDanmakuOfficialCollector
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.types.bili import DanmakuMessage, GiftMessage, GuardMessage, SuperChatMessage

GUARD_CMD = "LIVE_OPEN_PLATFORM_GUARD"
WATCHED_CMD = "LIVE_OPEN_PLATFORM_WATCHED_CHANGE"
CREDENTIALS = {"id_code": "x", "app_id": "y", "access_key": "z", "access_key_secret": "w"}


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event_name: str, payload, **kwargs):
        self.events.append((event_name, payload))


class _FakeQueue:
    """替代 asyncio.Queue：只记录 put 的载荷。"""

    def __init__(self) -> None:
        self.items: list[Any] = []

    async def put(self, item: Any) -> None:
        self.items.append(item)


@pytest.fixture
def collector() -> BiliDanmakuOfficialCollector:
    return BiliDanmakuOfficialCollector(config=dict(CREDENTIALS), event_bus=None)


def _wired(**overrides: Any) -> tuple[BiliDanmakuOfficialCollector, _FakeBus, Any]:
    bus = _FakeBus()
    return BiliDanmakuOfficialCollector(config={**CREDENTIALS, **overrides}, event_bus=bus), bus, _FakeQueue()


def _gift_raw() -> dict:
    """礼物原始形状（LIVE_OPEN_PLATFORM_SEND_GIFT）：3 连击、打折、21 级牌、舰长。"""
    return {
        "cmd": "LIVE_OPEN_PLATFORM_SEND_GIFT",
        "data": {
            "room_id": 1,
            "open_id": "u_gift",
            "uname": "礼物哥",
            "gift_id": 31036,
            "gift_name": "小星星",
            "gift_num": 1,
            "price": 1000,
            "r_price": 900,
            "paid": True,
            "fans_medal_level": 21,
            "fans_medal_name": "粉丝团",
            "fans_medal_wearing_status": True,
            "guard_level": 3,
            "timestamp": 1700000000,
            "msg_id": "msg_gift_1",
            "combo_gift": True,
            "combo_info": {"combo_base_num": 1, "combo_count": 3, "combo_id": "combo_abc", "combo_timeout": 0},
            "blind_gift": {"blind_gift_id": 0, "status": False},
        },
    }


def _sc_raw() -> dict:
    """SC 原始形状：50 元、置顶 60 秒。"""
    return {
        "cmd": "LIVE_OPEN_PLATFORM_SUPER_CHAT",
        "data": {
            "room_id": 1,
            "open_id": "u_sc",
            "uname": "SC哥",
            "message_id": 9527,
            "message": "加油",
            "rmb": 50,
            "timestamp": 1700000000,
            "start_time": 1700000000,
            "end_time": 1700000060,
            "guard_level": 0,
            "fans_medal_level": 12,
            "fans_medal_name": "牌子",
            "fans_medal_wearing_status": True,
            "msg_id": "msg_sc_1",
        },
    }


def _guard_raw(**extra: Any) -> dict:
    """上舰原始形状：舰长 1 个月 138000 金瓜子。"""
    data = {
        "open_id": "u_guard",
        "uname": "舰长酱",
        "guard_level": 3,
        "guard_num": 1,
        "guard_unit": "月",
        "price": 138000,
        "fans_medal_level": 7,
        "fans_medal_name": "灯牌",
        "fans_medal_wearing_status": True,
        "room_id": 1,
        "msg_id": "msg_guard_1",
        "timestamp": 1700000000,
        **extra,
    }
    return {"cmd": GUARD_CMD, "data": data}


def _danmaku_raw(**extra: Any) -> dict:
    return {
        "cmd": "LIVE_OPEN_PLATFORM_DM",
        "data": {"open_id": "u1", "uname": "甲", "msg": "hi", "timestamp": 1700000000, **extra},
    }


def test_gift_payload_amounts_combo_identity_and_raw_data(collector: BiliDanmakuOfficialCollector) -> None:
    payload = collector._create_payload(GiftMessage.from_dict(_gift_raw()))
    assert isinstance(payload, RoomMessagePayload) and payload.gift is not None
    gift = payload.gift
    assert (gift.count, gift.gift_id) == (3, 31036)  # max(gift_num=1, combo_count=3)，连击规则遗留维持现状
    assert (gift.unit_price, gift.total_price, gift.paid_price) == (
        1000,
        3000,
        2700,
    )  # 实付 = r_price 900 × 3，打折 paid < total
    assert gift.currency == "bilibili_gold_coin"
    assert (gift.combo_id, gift.combo_count, gift.combo_gift, gift.blind_gift_id) == ("combo_abc", 3, True, 0)
    assert (gift.guard_level, gift.fans_medal_level, gift.fans_medal_name) == (3, 21, "粉丝团")
    assert gift.msg_id == "msg_gift_1"
    assert json.loads(gift.raw_data)["data"]["combo_info"]["combo_id"] == "combo_abc"  # 完整原始 JSON，可重放补数

    free = _gift_raw()
    free["data"]["paid"] = False
    assert (
        collector._create_payload(GiftMessage.from_dict(free)).gift.currency == "bilibili_silver_coin"
    )  # 免费礼物不计付费


def test_super_chat_and_guard_payload_fields(collector: BiliDanmakuOfficialCollector) -> None:
    sc = collector._create_payload(SuperChatMessage.from_dict(_sc_raw())).sc
    assert (
        sc is not None and sc.total_price == 50_000 and sc.currency == "bilibili_gold_coin"
    )  # 人民币元 ×1000 换算金瓜子
    assert (sc.start_time, sc.end_time, sc.message_id, sc.fans_medal_level) == (1700000000, 1700000060, "9527", 12)
    assert sc.raw_data

    payload = collector._create_payload(GuardMessage.from_dict(_guard_raw()))
    guard = payload.guard
    assert guard is not None and (guard.guard_level, guard.guard_num, guard.guard_unit) == (3, 1, "月")
    assert (guard.total_price, guard.currency) == (138000, "bilibili_gold_coin")
    assert (guard.fans_medal_level, guard.fans_medal_name, guard.msg_id) == (7, "灯牌", "msg_guard_1")
    assert guard.raw_data and "舰长" in payload.content  # content 保留人读描述，下游识别据此优先回应


def test_platform_stamp_identity_guard_and_danmaku_message_id(collector: BiliDanmakuOfficialCollector) -> None:
    for message in (
        DanmakuMessage.from_dict(_danmaku_raw()),
        GiftMessage.from_dict(_gift_raw()),
        SuperChatMessage.from_dict(_sc_raw()),
        GuardMessage.from_dict(_guard_raw()),
    ):
        assert collector._create_payload(message).platform == "bilibili"  # 装配期常量，全部类型统一盖章

    missing_id = _gift_raw()
    missing_id["data"]["open_id"] = ""
    assert collector._create_payload(GiftMessage.from_dict(missing_id)) is None  # 缺 open_id 丢弃，防多用户退化混淆

    with_id = collector._create_payload(DanmakuMessage.from_dict(_danmaku_raw(msg="磨磨唧唧的", msg_id="9527")))
    assert with_id.message_id == "9527"  # 回复关联键（[id:] 拼接、reply_to 反查、对话页交织）全依赖此列，漏传即断链


async def test_guard_emits_guard_event_and_other_types_are_not_marked_guard() -> None:
    collector, bus, queue = _wired()
    await collector._handle_message_from_bili(_guard_raw(price=138, uname="舰长君", open_id="guard_user_1"), queue)
    ((event_name, payload),) = bus.events
    assert event_name == CoreEvents.ROOM_MESSAGE_GUARD == "room.message.guard"
    assert (payload.message_type, payload.user.id, payload.user.name) == ("guard", "guard_user_1", "舰长君")
    assert "舰长" in payload.content

    others, other_bus, other_queue = _wired()
    gift = {
        "cmd": "LIVE_OPEN_PLATFORM_SEND_GIFT",
        "data": {
            "open_id": "u2",
            "uname": "礼物哥",
            "gift_name": "辣条",
            "gift_num": 1,
            "timestamp": 2,
            "combo_info": {"combo_count": 1},
        },
    }
    sc = {
        "cmd": "LIVE_OPEN_PLATFORM_SUPER_CHAT",
        "data": {"open_id": "u3", "uname": "SC哥", "message": "冲", "rmb": 30, "timestamp": 3},
    }
    enter = {"cmd": "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER", "data": {"open_id": "u4", "uname": "路人", "timestamp": 4}}
    for message in (_danmaku_raw(), gift, sc, enter):
        await others._handle_message_from_bili(message, other_queue)
    assert [p.message_type for _, p in other_bus.events] == ["danmaku", "gift", "super_chat", "enter"]
    assert all(name != CoreEvents.ROOM_MESSAGE_GUARD for name, _ in other_bus.events)


@pytest.mark.parametrize(
    "message",
    [
        {"cmd": GUARD_CMD, "data": {}},  # 缺身份键：不构造兜底载荷，空 user_id 会让多个无 id 用户退化成同一行
        {
            "cmd": GUARD_CMD,
            "data": {"open_id": {"bad": "type"}, "uname": ["x"], "timestamp": "abc"},
        },  # 字段格式异常：顶层兜底记日志
        {"cmd": WATCHED_CMD},  # 缺 data 段：状态覆写不可逆，不用 0 冒充观测
        {"cmd": WATCHED_CMD, "data": {"watched_count": "not-a-number"}},
    ],
    ids=["guard-empty", "guard-bad-types", "watched-no-data", "watched-bad-type"],
)
async def test_malformed_messages_are_dropped_without_raising(message: dict) -> None:
    collector, bus, queue = _wired()
    await collector._handle_message_from_bili(message, queue)
    assert bus.events == []


@pytest.mark.parametrize(
    ("overrides", "emits"),
    [({}, True), ({"handle_watched_change_messages": False}, False), ({"emit_semantic_events": False}, False)],
    ids=["enabled", "handler-disabled", "semantic-gate-closed"],
)
async def test_watched_change_is_a_state_event_gated_by_config(overrides: dict, emits: bool) -> None:
    collector, bus, queue = _wired(**overrides)
    message = {
        "cmd": WATCHED_CMD,
        "data": {"channel_id": "123456", "watched_count": 12000, "watched_show": "1.2万人看过"},
    }
    await collector._handle_message_from_bili(message, queue)

    assert queue.items == []  # 观看数是状态推送非行为流：不进 collect() 消息队列
    if not emits:
        assert bus.events == []
        return
    ((event_name, payload),) = bus.events
    assert event_name == CoreEvents.ROOM_STATE_WATCHED_COUNT == "room.state.watched_count"
    assert (payload.platform, payload.audience_total, payload.watched_show) == ("bilibili", 12000, "1.2万人看过")


def test_collector_registration_metadata_and_semantic_event_switch() -> None:
    for cls, name in ((BiliDanmakuOfficialCollector, "bili_danmaku_official"), (BiliDanmakuCollector, "bili_danmaku")):
        assert issubclass(cls, BaseCollector) and cls.name == name and "Bilibili" in cls.description
    assert BiliDanmakuOfficialCollector.ConfigSchema(**CREDENTIALS).emit_semantic_events is True
    assert BiliDanmakuCollector.ConfigSchema(room_id=12345).emit_semantic_events is True
