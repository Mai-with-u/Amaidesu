"""
BiliDanmakuOfficialInputProvider 测试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.input.providers import BiliDanmakuOfficialInputProvider
from src.domains.input.shared.bili_messages import (
    BiliMessageType,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)


@pytest.fixture
def bili_official_config():
    """Bili官方弹幕配置"""
    return {
        "id_code": "test_room_id",
        "app_id": "test_app_id",
        "access_key": "test_access_key",
        "access_key_secret": "test_secret",
        "api_host": "https://test.biliapi.com",
        "message_cache_size": 100,
        "context_tags": ["tag1", "tag2"],
    }


class TestBiliDanmakuOfficialInputProvider:
    """测试 BiliDanmakuOfficialInputProvider"""

    def test_init_with_valid_config(self, bili_official_config):
        """测试有效配置初始化"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        assert provider.id_code == "test_room_id"
        assert provider.app_id == "test_app_id"
        assert provider.access_key == "test_access_key"
        assert provider.access_key_secret == "test_secret"
        assert provider.api_host == "https://test.biliapi.com"
        assert provider.context_tags == ["tag1", "tag2"]

    def test_init_with_missing_required_config(self):
        """测试缺少必需配置"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BiliDanmakuOfficialInputProvider({})

        with pytest.raises(ValidationError):
            BiliDanmakuOfficialInputProvider({"id_code": "test"})

    def test_init_with_default_api_host(self):
        """测试默认API host"""
        config = {
            "id_code": "test",
            "app_id": "test",
            "access_key": "test",
            "access_key_secret": "test",
        }
        provider = BiliDanmakuOfficialInputProvider(config)

        assert provider.api_host == "https://live-open.biliapi.com"

    def test_init_with_invalid_context_tags(self, bili_official_config):
        """测试无效的context_tags - Pydantic会验证类型"""
        from pydantic import ValidationError

        config = bili_official_config.copy()
        config["context_tags"] = "not_a_list"  # 应该是列表

        # Pydantic会验证并抛出ValidationError
        with pytest.raises(ValidationError):
            BiliDanmakuOfficialInputProvider(config)

    def test_init_with_empty_context_tags(self, bili_official_config):
        """测试空的context_tags列表"""
        config = bili_official_config.copy()
        config["context_tags"] = []

        provider = BiliDanmakuOfficialInputProvider(config)

        # 空列表应该被设置为None
        assert provider.context_tags is None

    def test_init_with_template_items(self, bili_official_config):
        """测试template_items配置"""
        config = bili_official_config.copy()
        config["enable_template_info"] = True
        config["template_items"] = {"key": "value"}

        provider = BiliDanmakuOfficialInputProvider(config)

        assert provider.template_items == {"key": "value"}

    def test_create_message_from_dict_danmaku(self, bili_official_config):
        """测试从字典创建弹幕消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": BiliMessageType.DANMAKU.value,
            "data": {
                "uname": "测试用户",
                "msg": "测试弹幕",
                "open_id": "test_open_id",
                "timestamp": 1234567890,
            },
        }

        result = provider._create_message_from_dict(message_data)

        assert isinstance(result, DanmakuMessage)
        assert result.uname == "测试用户"
        assert result.msg == "测试弹幕"

    def test_create_message_from_dict_enter(self, bili_official_config):
        """测试从字典创建进入消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": BiliMessageType.ENTER.value,
            "data": {
                "uname": "进入用户",
                "open_id": "test_open_id",
            },
        }

        result = provider._create_message_from_dict(message_data)

        assert isinstance(result, EnterMessage)
        assert result.uname == "进入用户"

    def test_create_message_from_dict_gift(self, bili_official_config):
        """测试从字典创建礼物消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": BiliMessageType.GIFT.value,
            "data": {
                "uname": "送礼用户",
                "gift_name": "小电视",
                "gift_num": 10,
                "price": 100,
            },
        }

        result = provider._create_message_from_dict(message_data)

        assert isinstance(result, GiftMessage)
        assert result.uname == "送礼用户"
        assert result.gift_name == "小电视"
        assert result.gift_num == 10

    def test_create_message_from_dict_guard(self, bili_official_config):
        """测试从字典创建大航海消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": BiliMessageType.GUARD.value,
            "data": {
                "uname": "舰长用户",
                "guard_level": 3,
                "price": 198000,
            },
        }

        result = provider._create_message_from_dict(message_data)

        assert isinstance(result, GuardMessage)
        assert result.uname == "舰长用户"
        assert result.guard_level == 3

    def test_create_message_from_dict_superchat(self, bili_official_config):
        """测试从字典创建SC消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": BiliMessageType.SUPER_CHAT.value,
            "data": {
                "uname": "SC用户",
                "message": "测试SC消息",
                "rmb": 100,
            },
        }

        result = provider._create_message_from_dict(message_data)

        assert isinstance(result, SuperChatMessage)
        assert result.uname == "SC用户"
        assert result.message == "测试SC消息"
        assert result.rmb == 100

    def test_create_message_from_dict_unknown(self, bili_official_config):
        """测试从字典创建未知消息类型"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        message_data = {
            "cmd": "UNKNOWN_CMD",
            "data": {},
        }

        result = provider._create_message_from_dict(message_data)

        assert result is None

    def test_create_normalized_message_danmaku(self, bili_official_config):
        """测试从弹幕消息创建 NormalizedMessage"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        danmaku = DanmakuMessage(
            cmd=BiliMessageType.DANMAKU.value,
            uname="测试用户",
            msg="测试弹幕内容",
            fans_medal_level=20,
            guard_level=0,
        )

        result = provider._create_normalized_message(danmaku)

        assert result.text == "测试弹幕内容"
        assert result.source == "bili_danmaku_official"
        assert result.data_type == "text"
        assert result.raw == danmaku

    def test_create_normalized_message_enter(self, bili_official_config):
        """测试从进入消息创建 NormalizedMessage"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        enter = EnterMessage(
            cmd=BiliMessageType.ENTER.value,
            uname="进入用户",
        )

        result = provider._create_normalized_message(enter)

        assert "进入用户" in result.text
        assert "进入了直播间" in result.text
        assert result.data_type == "enter"
        assert result.importance == 0.1

    def test_create_normalized_message_gift(self, bili_official_config):
        """测试从礼物消息创建 NormalizedMessage"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        gift = GiftMessage(
            cmd=BiliMessageType.GIFT.value,
            uname="送礼用户",
            gift_name="小电视",
            gift_num=10,
            price=100,
            paid=True,
        )

        result = provider._create_normalized_message(gift)

        assert "送礼用户" in result.text
        assert "10 个" in result.text
        assert "小电视" in result.text
        assert result.data_type == "gift"

    def test_create_normalized_message_guard(self, bili_official_config):
        """测试从大航海消息创建 NormalizedMessage"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 舰长
        guard = GuardMessage(
            cmd=BiliMessageType.GUARD.value,
            guard_level=3,
        )
        guard.user_info.uname = "舰长用户"

        result = provider._create_normalized_message(guard)

        assert "舰长用户" in result.text
        assert "舰长" in result.text
        assert result.data_type == "guard"
        assert result.importance == 0.8

    def test_create_normalized_message_superchat(self, bili_official_config):
        """测试从SC消息创建 NormalizedMessage"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        sc = SuperChatMessage(
            cmd=BiliMessageType.SUPER_CHAT.value,
            uname="SC用户",
            message="这是一条SC消息",
            rmb=100,
        )

        result = provider._create_normalized_message(sc)

        assert result.text == "这是一条SC消息"
        assert result.data_type == "super_chat"
        assert result.importance == 1.0  # 0.5 + 100/100 = 1.5, min(1.5, 1.0) = 1.0

    def test_calculate_danmaku_importance(self, bili_official_config):
        """测试弹幕重要性计算"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 无粉丝牌无大航海
        msg1 = DanmakuMessage(
            cmd=BiliMessageType.DANMAKU.value,
            fans_medal_level=0,
            guard_level=0,
        )
        assert provider._calculate_danmaku_importance(msg1) == 0.5

        # 有粉丝牌
        msg2 = DanmakuMessage(
            cmd=BiliMessageType.DANMAKU.value,
            fans_medal_level=20,
            guard_level=0,
        )
        assert 0.5 < provider._calculate_danmaku_importance(msg2) <= 1.0

        # 有大航海
        msg3 = DanmakuMessage(
            cmd=BiliMessageType.DANMAKU.value,
            fans_medal_level=0,
            guard_level=1,  # 总督
        )
        assert provider._calculate_danmaku_importance(msg3) == 0.8

    def test_calculate_gift_importance(self, bili_official_config):
        """测试礼物重要性计算"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 免费礼物，1个（quantity_bonus = 0.1）
        msg1 = GiftMessage(
            cmd=BiliMessageType.GIFT.value,
            price=0,
            gift_num=1,
            paid=False,
        )
        # base=0, quantity_bonus=0.1, paid_bonus=0 -> 0.1
        assert provider._calculate_gift_importance(msg1) == 0.1

        # 付费礼物
        msg2 = GiftMessage(
            cmd=BiliMessageType.GIFT.value,
            price=1000,
            gift_num=5,
            paid=True,
        )
        importance = provider._calculate_gift_importance(msg2)
        assert 0 < importance <= 1.0

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_success(self, bili_official_config):
        """测试成功处理B站消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 创建队列
        message_queue = asyncio.Queue()

        # 模拟弹幕消息数据
        message_data = {
            "cmd": BiliMessageType.DANMAKU.value,
            "data": {
                "uname": "测试用户",
                "msg": "测试弹幕",
                "open_id": "test_open_id",
            },
        }

        # 调用处理方法
        await provider._handle_message_from_bili(message_data, message_queue)

        # 从队列获取结果
        result = await message_queue.get()

        # 验证
        assert result.source == "bili_danmaku_official"
        assert result.data_type == "text"
        assert result.text == "测试弹幕"

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_filtered(self, bili_official_config):
        """测试消息被过滤"""
        config = bili_official_config.copy()
        config["handle_enter_messages"] = False  # 禁用进入消息
        provider = BiliDanmakuOfficialInputProvider(config)

        # 创建队列
        message_queue = asyncio.Queue()

        # 模拟进入消息
        message_data = {
            "cmd": BiliMessageType.ENTER.value,
            "data": {
                "uname": "进入用户",
            },
        }

        # 调用处理方法
        await provider._handle_message_from_bili(message_data, message_queue)

        # 验证队列中没有数据
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(message_queue.get(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_unknown_cmd(self, bili_official_config):
        """测试未知命令消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 创建队列
        message_queue = asyncio.Queue()

        # 未知命令
        message_data = {
            "cmd": "UNKNOWN_CMD",
            "data": {},
        }

        # 调用处理方法
        await provider._handle_message_from_bili(message_data, message_queue)

        # 验证队列中没有数据
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(message_queue.get(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_cleanup_with_websocket_client(self, bili_official_config):
        """测试清理WebSocket客户端"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock websocket_client
        mock_ws_client = MagicMock()
        mock_ws_client.close = AsyncMock()
        provider.websocket_client = mock_ws_client

        await provider._cleanup()

        # 验证WebSocket被关闭
        mock_ws_client.close.assert_called_once()
        # 验证websocket_client被设置为None
        assert provider.websocket_client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_websocket_client(self, bili_official_config):
        """测试没有WebSocket客户端时的清理"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # 应该不抛出异常
        await provider._cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_exception_handling(self, bili_official_config):
        """测试清理时的异常处理"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock websocket_client抛出异常
        mock_ws_client = MagicMock()
        mock_ws_client.close = AsyncMock(side_effect=Exception("Close error"))
        provider.websocket_client = mock_ws_client

        # 应该不抛出异常，继续执行
        await provider._cleanup()

        # websocket_client仍应被设置为None（在finally块中）
        assert provider.websocket_client is None
