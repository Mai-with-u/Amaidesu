"""
Bili 弹幕消息测试
"""


from src.domains.input.shared.bili_messages.danmaku import DanmakuMessage
from src.domains.input.shared.bili_messages.gift import GiftMessage
from src.domains.input.shared.bili_messages.guard import GuardMessage
from src.domains.input.shared.bili_messages.superchat import SuperChatMessage
from src.domains.input.shared.bili_messages.enter import EnterMessage


class TestDanmakuMessage:
    """弹幕消息测试"""

    def test_from_dict_basic(self):
        """测试从字典创建弹幕消息"""
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

    def test_from_dict_with_fans_medal(self):
        """测试带粉丝牌的弹幕消息"""
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

    def test_from_dict_with_guard(self):
        """测试大航海用户弹幕"""
        data = {
            "cmd": "LIVE_OPEN_PLATFORM_DM",
            "data": {
                "uname": "舰长用户",
                "msg": "弹幕",
                "guard_level": 3,
            },
        }

        msg = DanmakuMessage.from_dict(data)

        assert msg.guard_level == 3

    def test_from_dict_with_reply(self):
        """测试回复弹幕"""
        data = {
            "cmd": "LIVE_OPEN_PLATFORM_DM",
            "data": {
                "uname": "回复用户",
                "msg": "回复内容",
                "reply_uname": "原用户",
            },
        }

        msg = DanmakuMessage.from_dict(data)

        assert msg.reply_uname == "原用户"

    def test_from_dict_with_defaults(self):
        """测试默认值"""
        data = {
            "cmd": "LIVE_OPEN_PLATFORM_DM",
            "data": {},
        }

        msg = DanmakuMessage.from_dict(data)

        assert msg.uname == ""
        assert msg.msg == ""
        assert msg.room_id == 0
        assert msg.fans_medal_level == 0
        assert msg.guard_level == 0
        assert msg.fans_medal_wearing_status is False


class TestGiftMessage:
    """礼物消息测试"""

    def test_from_dict_basic(self):
        """测试从字典创建礼物消息"""
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
        assert msg.room_id == 12345


class TestGuardMessage:
    """大航海消息测试"""

    def test_from_dict_basic(self):
        """测试从字典创建大航海消息"""
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
        assert msg.guard_num == 1
        assert msg.price == 198
        assert msg.room_id == 12345


class TestSuperChatMessage:
    """醒目留言测试"""

    def test_from_dict_basic(self):
        """测试从字典创建醒目留言"""
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
        assert msg.room_id == 12345


class TestEnterMessage:
    """入场消息测试"""

    def test_from_dict_basic(self):
        """测试从字典创建入场消息"""
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
