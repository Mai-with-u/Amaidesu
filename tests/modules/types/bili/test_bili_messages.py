"""B 站消息类型：命令名枚举、各消息 ``from_dict`` 解析与缺字段默认值、消息类型过滤配置（``src.modules.types.bili``）"""

import pytest

from src.modules.types.bili import (
    BiliMessageType,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
    WatchedChangeMessage,
)
from src.modules.types.bili.config import BiliMessageTypeConfig

OFFICIAL_COMMANDS = {
    "DANMAKU": "LIVE_OPEN_PLATFORM_DM",
    "ENTER": "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER",
    "GIFT": "LIVE_OPEN_PLATFORM_SEND_GIFT",
    "GUARD": "LIVE_OPEN_PLATFORM_GUARD",
    "SUPER_CHAT": "LIVE_OPEN_PLATFORM_SUPER_CHAT",
    "WATCHED_CHANGE": "LIVE_OPEN_PLATFORM_WATCHED_CHANGE",
}


def test_enum_values_match_the_official_api_command_names() -> None:
    assert {name: BiliMessageType[name].value for name in OFFICIAL_COMMANDS} == OFFICIAL_COMMANDS


@pytest.mark.parametrize(
    ("message_cls", "data", "expected"),
    [
        (
            WatchedChangeMessage,
            {"channel_id": "123456", "watched_count": 12000, "watched_show": "1.2万人看过"},
            {"channel_id": "123456", "watched_count": 12000, "watched_show": "1.2万人看过"},
        ),
        (
            DanmakuMessage,
            {"uname": "测试用户", "msg": "你好", "room_id": 12345, "timestamp": 1700000000},
            {"uname": "测试用户", "msg": "你好", "room_id": 12345, "timestamp": 1700000000},
        ),
        (
            DanmakuMessage,
            {"uname": "粉丝", "fans_medal_level": 10, "fans_medal_name": "粉丝牌", "fans_medal_wearing_status": True},
            {"fans_medal_level": 10, "fans_medal_name": "粉丝牌", "fans_medal_wearing_status": True},
        ),
        (GiftMessage, {"uname": "送礼用户", "gift_name": "辣条", "gift_num": 1}, {"gift_name": "辣条", "gift_num": 1}),
        (GuardMessage, {"uname": "舰长", "guard_level": 3, "price": 198}, {"guard_level": 3, "price": 198}),
        (SuperChatMessage, {"uname": "SC用户", "message": "醒目留言", "rmb": 30}, {"message": "醒目留言", "rmb": 30}),
        (EnterMessage, {"uname": "入场用户", "room_id": 12345}, {"uname": "入场用户", "room_id": 12345}),
    ],
)
def test_from_dict_reads_the_payload_fields(message_cls, data, expected) -> None:
    message = message_cls.from_dict({"cmd": "ANY", "data": data})

    assert {field: getattr(message, field) for field in expected} == expected


def test_from_dict_with_missing_fields_falls_back_to_defaults_instead_of_raising() -> None:
    raw = {"cmd": "x", "data": {}}
    watched = WatchedChangeMessage.from_dict(raw)
    assert watched.raw_data == raw  # 原始报文随消息保留
    danmaku = DanmakuMessage.from_dict({"cmd": "x", "data": {}})

    assert (watched.watched_count, watched.watched_show) == (0, "")
    assert (danmaku.uname, danmaku.msg, danmaku.fans_medal_wearing_status) == ("", "", False)


def test_message_type_config_enables_every_type_by_default() -> None:
    """默认全部启用。注：``disable_via_key`` 路径存在变量遮蔽缺陷，这里只覆盖默认行为。"""
    config = BiliMessageTypeConfig({})

    assert all(config.should_handle(message_type.value) for message_type in BiliMessageType)
    assert BiliMessageType.DANMAKU.value in config.get_enabled_types() and len(config.get_enabled_types()) >= 5
