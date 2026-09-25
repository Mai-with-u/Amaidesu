"""B 站采集器 payload 字段断言单测

喂真实形状的原始 dict → 断言 payload 全字段(金额口径/身份快照/连击/
盲盒/raw_data/平台常量),兜住"采集层解析了但填错字段"的回归。
"""

from __future__ import annotations

import json

import pytest

from src.modules.collectors.bilibili.official.bili_danmaku_official_collector import (
    BiliDanmakuOfficialCollector,
)
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.types.bili import (
    DanmakuMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)


@pytest.fixture
def collector() -> BiliDanmakuOfficialCollector:
    return BiliDanmakuOfficialCollector(
        config={"id_code": "x", "app_id": "y", "access_key": "z", "access_key_secret": "w"},
        event_bus=None,
    )


def _gift_raw() -> dict:
    """礼物消息原始形状(B 站 LIVE_OPEN_PLATFORM_SEND_GIFT):3 连击、打折、21 级牌、舰长。"""
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
    """SC 消息原始形状:50 元、置顶 60 秒。"""
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


def _guard_raw() -> dict:
    """上舰消息原始形状:舰长 1 个月 138000 金瓜子。"""
    return {
        "cmd": "LIVE_OPEN_PLATFORM_GUARD",
        "data": {
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
        },
    }


class TestGiftPayloadFields:
    def test_amount_fields(self, collector: BiliDanmakuOfficialCollector) -> None:
        """标价/实付/币种/数量:total = 单价 × 数量,打折时 paid < total。"""
        payload = collector._create_payload(GiftMessage.from_dict(_gift_raw()))
        assert isinstance(payload, RoomMessagePayload) and payload.gift is not None
        gift = payload.gift
        assert gift.count == 3  # max(gift_num=1, combo_count=3),连击规则遗留维持现状
        assert gift.gift_id == 31036
        assert gift.unit_price == 1000
        assert gift.total_price == 3000
        assert gift.paid_price == 2700  # r_price 900 × 3,打折实付
        assert gift.paid_price < gift.total_price
        assert gift.currency == "bilibili_gold_coin"

    def test_combo_and_blind_and_identity_snapshot(self, collector: BiliDanmakuOfficialCollector) -> None:
        """连击/盲盒/身份快照(msg_id/fans_medal/guard_level)逐字段透传。"""
        gift = collector._create_payload(GiftMessage.from_dict(_gift_raw())).gift
        assert gift is not None
        assert gift.combo_id == "combo_abc"
        assert gift.combo_count == 3
        assert gift.combo_gift is True
        assert gift.blind_gift_id == 0
        assert gift.guard_level == 3
        assert gift.fans_medal_level == 21
        assert gift.fans_medal_name == "粉丝团"
        assert gift.msg_id == "msg_gift_1"

    def test_raw_data_roundtrip(self, collector: BiliDanmakuOfficialCollector) -> None:
        """raw_data 携带完整原始 JSON(可修复解析后重放补数)。"""
        payload = collector._create_payload(GiftMessage.from_dict(_gift_raw()))
        gift = payload.gift
        assert gift is not None and gift.raw_data
        assert json.loads(gift.raw_data)["data"]["combo_info"]["combo_id"] == "combo_abc"

    def test_free_gift_is_silver(self, collector: BiliDanmakuOfficialCollector) -> None:
        """免费礼物(paid=False)币种 = 银瓜子,照常落库不计付费。"""
        raw = _gift_raw()
        raw["data"]["paid"] = False
        gift = collector._create_payload(GiftMessage.from_dict(raw)).gift
        assert gift is not None
        assert gift.currency == "bilibili_silver_coin"


class TestSuperChatPayloadFields:
    def test_amount_converted_to_gold_coin(self, collector: BiliDanmakuOfficialCollector) -> None:
        """SC 官方单位是人民币元,×1000 换算金瓜子(整数无损)。"""
        payload = collector._create_payload(SuperChatMessage.from_dict(_sc_raw()))
        assert payload.sc is not None
        sc = payload.sc
        assert sc.total_price == 50_000
        assert sc.currency == "bilibili_gold_coin"
        assert sc.start_time == 1700000000
        assert sc.end_time == 1700000060
        assert sc.message_id == "9527"
        assert sc.fans_medal_level == 12
        assert sc.raw_data


class TestGuardPayloadFields:
    def test_structured_fields(self, collector: BiliDanmakuOfficialCollector) -> None:
        """上舰结构化载荷:等级/周期原值/总额(= price)/身份快照。"""
        payload = collector._create_payload(GuardMessage.from_dict(_guard_raw()))
        assert payload.guard is not None
        guard = payload.guard
        assert guard.guard_level == 3
        assert guard.guard_num == 1
        assert guard.guard_unit == "月"
        assert guard.total_price == 138000
        assert guard.currency == "bilibili_gold_coin"
        assert guard.fans_medal_level == 7
        assert guard.fans_medal_name == "灯牌"
        assert guard.msg_id == "msg_guard_1"
        assert guard.raw_data
        # content 保留人读描述(下游识别做优先回应)
        assert "舰长" in payload.content


class TestPlatformAndIdentity:
    def test_platform_stamped_on_all_types(self, collector: BiliDanmakuOfficialCollector) -> None:
        """platform 是装配期常量,全部消息类型统一盖章。"""
        for msg in (
            DanmakuMessage.from_dict({"cmd": "LIVE_OPEN_PLATFORM_DM", "data": {"open_id": "u1", "uname": "甲", "msg": "hi", "timestamp": 1700000000}}),
            GiftMessage.from_dict(_gift_raw()),
            SuperChatMessage.from_dict(_sc_raw()),
            GuardMessage.from_dict(_guard_raw()),
        ):
            payload = collector._create_payload(msg)
            assert payload is not None
            assert payload.platform == "bilibili"

    def test_missing_open_id_dropped(self, collector: BiliDanmakuOfficialCollector) -> None:
        """open_id 缺失:丢弃该消息(防多用户退化混淆,不落库不广播)。"""
        raw = _gift_raw()
        raw["data"]["open_id"] = ""
        assert collector._create_payload(GiftMessage.from_dict(raw)) is None
