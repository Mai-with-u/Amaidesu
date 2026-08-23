"""
B 站消息类型测试（v2 / Wave 5 迁移后）

迁移自 ``tests/stages/input/shared/bili_messages/test_bili_messages.py``，现
通过 ``src.modules.types.bili`` 公共共享类型验证。

W5 后 ``src.stages.input.shared.bili_messages`` 成为 re-export 垫片（向后兼容），
新代码统一从 ``src.modules.types.bili`` 导入。
"""

from src.modules.types.bili import (
    BiliMessageType,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)
from src.modules.types.bili.config import BiliMessageTypeConfig


class TestBiliMessageType:
    """BiliMessageType 枚举测试"""

    def test_enum_values_match_official_api(self) -> None:
        """枚举值与 B 站官方 API 命令名一致"""
        assert BiliMessageType.DANMAKU.value == "LIVE_OPEN_PLATFORM_DM"
        assert BiliMessageType.ENTER.value == "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER"
        assert BiliMessageType.GIFT.value == "LIVE_OPEN_PLATFORM_SEND_GIFT"
        assert BiliMessageType.GUARD.value == "LIVE_OPEN_PLATFORM_GUARD"
        assert BiliMessageType.SUPER_CHAT.value == "LIVE_OPEN_PLATFORM_SUPER_CHAT"


class TestDanmakuMessage:
    """弹幕消息测试"""

    def test_from_dict_basic(self) -> None:
        data = {
            "cmd": "LIVE_OPEN_PLATFORM_DM",
            "data": {
                "uname": "测试用户",
                "msg": "你好",
                "room_id": 12345,
                "timestamp": 1700000000,
            },
        }
        msg = DanmakuMessage.from_dict(data)
        assert msg.uname == "测试用户"
        assert msg.msg == "你好"
        assert msg.room_id == 12345
        assert msg.timestamp == 1700000000

    def test_from_dict_with_fans_medal(self) -> None:
        data = {
            "cmd": "LIVE_OPEN_PLATFORM_DM",
            "data": {
                "uname": "粉丝用户",
                "msg": "支持主播",
                "fans_medal_level": 10,
                "fans_medal_name": "粉丝牌",
                "fans_medal_wearing_status": True,
            },
        }
        msg = DanmakuMessage.from_dict(data)
        assert msg.fans_medal_level == 10
        assert msg.fans_medal_name == "粉丝牌"
        assert msg.fans_medal_wearing_status is True

    def test_from_dict_with_defaults(self) -> None:
        data = {"cmd": "LIVE_OPEN_PLATFORM_DM", "data": {}}
        msg = DanmakuMessage.from_dict(data)
        assert msg.uname == ""
        assert msg.msg == ""
        assert msg.fans_medal_wearing_status is False


class TestGiftMessage:
    """礼物消息测试"""

    def test_from_dict_basic(self) -> None:
        data = {
            "cmd": "SEND_GIFT",
            "data": {
                "uname": "送礼用户",
                "gift_name": "辣条",
                "gift_num": 1,
                "room_id": 12345,
            },
        }
        msg = GiftMessage.from_dict(data)
        assert msg.uname == "送礼用户"
        assert msg.gift_name == "辣条"
        assert msg.gift_num == 1


class TestGuardMessage:
    """大航海消息测试"""

    def test_from_dict_basic(self) -> None:
        data = {
            "cmd": "GUARD_BUY",
            "data": {
                "uname": "舰长",
                "guard_level": 3,
                "guard_num": 1,
                "price": 198,
                "room_id": 12345,
            },
        }
        msg = GuardMessage.from_dict(data)
        assert msg.uname == "舰长"
        assert msg.guard_level == 3
        assert msg.price == 198


class TestSuperChatMessage:
    """醒目留言测试"""

    def test_from_dict_basic(self) -> None:
        data = {
            "cmd": "SUPER_CHAT_MESSAGE",
            "data": {
                "uname": "SC用户",
                "message": "醒目留言内容",
                "rmb": 30,
                "room_id": 12345,
            },
        }
        msg = SuperChatMessage.from_dict(data)
        assert msg.uname == "SC用户"
        assert msg.message == "醒目留言内容"
        assert msg.rmb == 30


class TestEnterMessage:
    """入场消息测试"""

    def test_from_dict_basic(self) -> None:
        data = {
            "cmd": "INTERACT_WORD",
            "data": {
                "uname": "入场用户",
                "room_id": 12345,
                "timestamp": 1700000000,
            },
        }
        msg = EnterMessage.from_dict(data)
        assert msg.uname == "入场用户"
        assert msg.room_id == 12345
        assert msg.timestamp == 1700000000


class TestBiliMessageTypeConfig:
    """消息类型过滤配置测试（v2 保留）

    注：``disable_via_key`` 路径在原 ``stages/input/shared/bili_messages/config.py``
    中存在变量遮蔽 bug（W5 verbatim 保留，未修）。本测试仅覆盖默认行为。
    """

    def test_default_all_enabled(self) -> None:
        """默认所有消息类型都启用"""
        config = BiliMessageTypeConfig({})
        assert config.should_handle(BiliMessageType.DANMAKU.value)
        assert config.should_handle(BiliMessageType.GIFT.value)
        assert config.should_handle(BiliMessageType.GUARD.value)
        assert config.should_handle(BiliMessageType.SUPER_CHAT.value)
        assert config.should_handle(BiliMessageType.ENTER.value)

    def test_get_enabled_types(self) -> None:
        """获取所有启用的消息类型"""
        config = BiliMessageTypeConfig({})
        enabled = config.get_enabled_types()
        assert BiliMessageType.DANMAKU.value in enabled
        assert len(enabled) >= 5
