"""DanmakuWidgetMessage.to_display_text 渲染测试。

验证 widget 展示文本与登记表 display_template 字节级一致（收编后输出不变），
覆盖 5 类消息 + REPLY 兜底 + guard 等级映射。
"""

from datetime import datetime

from src.modules.dashboard.widget.models import DanmakuWidgetMessage, MessageType


def _make_msg(**overrides) -> DanmakuWidgetMessage:
    base = {
        "user_name": "观众A",
        "content": "测试内容",
        "message_type": MessageType.TEXT,
        "timestamp": datetime(2026, 1, 1),
    }
    base.update(overrides)
    return DanmakuWidgetMessage(**base)


def test_text_displays_content():
    msg = _make_msg(content="普通弹幕文本")
    assert msg.to_display_text() == "普通弹幕文本"


def test_gift_displays_template_with_defaults():
    msg = _make_msg(message_type=MessageType.GIFT, gift_name="辣条", gift_count=5)
    assert msg.to_display_text() == "送出 辣条 x5"


def test_gift_defaults_name_and_count():
    msg = _make_msg(message_type=MessageType.GIFT)
    assert msg.to_display_text() == "送出 礼物 x1"


def test_super_chat_displays_price_and_message():
    msg = _make_msg(
        message_type=MessageType.SUPER_CHAT,
        content="原内容",
        sc_price=30.0,
        sc_message="醒目留言正文",
    )
    assert msg.to_display_text() == "¥30.0 醒目留言正文"


def test_super_chat_falls_back_to_content():
    msg = _make_msg(message_type=MessageType.SUPER_CHAT, sc_price=30.0)
    assert msg.to_display_text() == "¥30.0 测试内容"


def test_guard_displays_name_by_level():
    msg = _make_msg(message_type=MessageType.GUARD, guard_level=1)
    assert msg.to_display_text() == "开通了 总督"


def test_guard_default_level_is_captain():
    msg = _make_msg(message_type=MessageType.GUARD)
    assert msg.to_display_text() == "开通了 舰长"


def test_enter_displays_fixed_text():
    msg = _make_msg(message_type=MessageType.ENTER)
    assert msg.to_display_text() == "进入了直播间"


def test_reply_falls_back_to_content():
    msg = _make_msg(message_type=MessageType.REPLY, content="AI 回复内容")
    assert msg.to_display_text() == "AI 回复内容"
