"""Tests for dashboard overlay module."""

import pytest

from src.modules.dashboard.overlay.models import MessageType, OverlayConfig, OverlayMessage


class TestOverlayMessage:
    """Tests for OverlayMessage model."""

    def test_create_danmaku_message(self):
        msg = OverlayMessage(
            user_name="测试用户",
            user_id="12345",
            content="这是一条测试弹幕",
            message_type=MessageType.DANMAKU,
        )
        assert msg.user_name == "测试用户"
        assert msg.message_type == "danmaku"
        assert msg.content == "这是一条测试弹幕"

    def test_create_gift_message(self):
        msg = OverlayMessage(
            user_name="土豪用户",
            user_id="99999",
            content="送出了礼物",
            message_type=MessageType.GIFT,
            gift_name="小电视",
            gift_count=10,
            gift_price=100.0,
        )
        assert msg.gift_name == "小电视"
        assert msg.gift_count == 10

        assert msg.to_display_text() == "送出 小电视 x10"

    def test_create_superchat_message(self):
        msg = OverlayMessage(
            user_name="SC用户",
            user_id="88888",
            content="这是一条SC消息",
            message_type=MessageType.SUPER_CHAT,
            sc_price=100.0,
            sc_message="支持主播！",
        )
        assert msg.sc_price == 100.0
        assert "¥100" in msg.to_display_text()

    def test_to_display_text_danmaku(self):
        msg = OverlayMessage(
            user_name="用户A",
            content="普通弹幕内容",
            message_type=MessageType.DANMAKU,
        )
        assert msg.to_display_text() == "普通弹幕内容"

    def test_to_display_text_guard(self):
        msg = OverlayMessage(
            user_name="舰长用户",
            content="开通了大航海",
            message_type=MessageType.GUARD,
            guard_level=3,
        )
        assert "舰长" in msg.to_display_text()


class TestOverlayConfig:
    """Tests for OverlayConfig model."""

    def test_default_config(self):
        config = OverlayConfig()
        assert config.enabled is True
        assert config.max_messages == 30
        assert config.show_danmaku is True
        assert config.show_gift is True
        assert config.show_super_chat is True
        assert config.show_guard is True
        assert config.show_enter is False

    def test_custom_config(self):
        config = OverlayConfig(
            max_messages=50,
            show_enter=True,
            min_importance=0.3,
        )
        assert config.max_messages == 50
        assert config.show_enter is True
        assert config.min_importance == 0.3

    def test_config_validation(self):
        with pytest.raises(ValueError):
            OverlayConfig(max_messages=200)  # 超过最大值

        with pytest.raises(ValueError):
            OverlayConfig(min_importance=1.5)  # 超过最大值
