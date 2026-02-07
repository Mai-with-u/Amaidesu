"""
BiliDanmakuInputProvider 测试
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.domains.input.providers.bili_danmaku import BiliDanmakuInputProvider
from src.core.base.raw_data import RawData


@pytest.fixture
def bili_config():
    """BiliDanmaku配置"""
    return {
        "room_id": 123456,
        "poll_interval": 1,
        "message_config": {
            "platform": "bilibili",
            "default_user_id": "test_user",
        }
    }


class TestBiliDanmakuInputProvider:
    """测试 BiliDanmakuInputProvider"""

    def test_init_with_valid_config(self, bili_config):
        """测试有效配置初始化"""
        provider = BiliDanmakuInputProvider(bili_config)

        assert provider.room_id == 123456
        assert provider.poll_interval == 1
        assert "bilibili.com" in str(provider.api_url)
        assert str(provider.room_id) in str(provider.api_url)

    def test_init_with_invalid_room_id(self):
        """测试无效room_id"""
        with pytest.raises(ValueError, match="Invalid or missing 'room_id'"):
            BiliDanmakuInputProvider({"room_id": -1})

        with pytest.raises(ValueError, match="Invalid or missing 'room_id'"):
            BiliDanmakuInputProvider({"room_id": "abc"})

        with pytest.raises(ValueError, match="Invalid or missing 'room_id'"):
            BiliDanmakuInputProvider({})

    def test_init_without_aiohttp(self, bili_config):
        """测试缺少aiohttp依赖"""
        with patch('src.domains.input.providers.bili_danmaku.bili_danmaku_provider.aiohttp', None):
            with pytest.raises(ImportError, match="aiohttp is required"):
                BiliDanmakuInputProvider(bili_config)

    @pytest.mark.asyncio
    async def test_create_danmaku_raw_data(self, bili_config):
        """测试创建弹幕RawData"""
        provider = BiliDanmakuInputProvider(bili_config)

        # 模拟API返回的弹幕数据
        api_danmaku = {
            "text": "测试弹幕",
            "nickname": "测试用户",
            "uid": 12345,
            "check_info": {
                "ts": 1234567890.0
            }
        }

        raw_data = await provider._create_danmaku_raw_data(api_danmaku)

        assert raw_data is not None
        assert raw_data.content["text"] == "测试弹幕"
        assert raw_data.content["nickname"] == "测试用户"
        assert raw_data.content["user_id"] == "12345"
        assert raw_data.source == "bili_danmaku"
        assert raw_data.data_type == "text"
        assert raw_data.metadata["nickname"] == "测试用户"
        assert raw_data.metadata["room_id"] == 123456

    @pytest.mark.asyncio
    async def test_create_danmaku_raw_data_empty_text(self, bili_config):
        """测试空文本弹幕"""
        provider = BiliDanmakuInputProvider(bili_config)

        api_danmaku = {
            "text": "",
            "nickname": "测试用户",
            "uid": 12345,
            "check_info": {"ts": 1234567890.0}
        }

        raw_data = await provider._create_danmaku_raw_data(api_danmaku)

        # 空文本应该返回None
        assert raw_data is None

    @pytest.mark.asyncio
    async def test_create_danmaku_raw_data_with_message_config(self, bili_config):
        """测试带完整消息配置的RawData创建"""
        bili_config["message_config"].update({
            "enable_group_info": True,
            "group_id": "test_group",
            "group_name": "测试群组",
            "content_format": ["text", "emoji"],
            "accept_format": ["text"],
        })

        provider = BiliDanmakuInputProvider(bili_config)

        api_danmaku = {
            "text": "带配置的弹幕",
            "nickname": "配置用户",
            "uid": 67890,
            "check_info": {"ts": 1234567890.0}
        }

        raw_data = await provider._create_danmaku_raw_data(api_danmaku)

        assert raw_data is not None
        # 检查消息配置
        msg_config = raw_data.content["message_config"]
        assert msg_config["user_info"]["platform"] == "bilibili"
        assert msg_config["user_info"]["user_nickname"] == "配置用户"

        # 检查群组信息
        assert msg_config["group_info"]["group_id"] == "test_group"
        assert msg_config["group_info"]["group_name"] == "测试群组"

        # 检查格式信息
        assert msg_config["format_info"]["content_format"] == ["text", "emoji"]

    @pytest.mark.asyncio
    async def test_create_danmaku_raw_data_batch(self, bili_config):
        """测试批量创建弹幕RawData"""
        provider = BiliDanmakuInputProvider(bili_config)

        # 模拟多个API弹幕数据
        api_danmakus = [
            {
                "text": "弹幕1",
                "nickname": "用户1",
                "uid": 111,
                "check_info": {"ts": 1500.0}
            },
            {
                "text": "弹幕2",
                "nickname": "用户2",
                "uid": 222,
                "check_info": {"ts": 2000.0}
            },
            {
                "text": "弹幕3",
                "nickname": "用户3",
                "uid": 333,
                "check_info": {"ts": 2500.0}
            }
        ]

        raw_data_list = []
        for api_danmaku in api_danmakus:
            raw_data = await provider._create_danmaku_raw_data(api_danmaku)
            if raw_data:
                raw_data_list.append(raw_data)

        assert len(raw_data_list) == 3
        assert raw_data_list[0].content["text"] == "弹幕1"
        assert raw_data_list[1].content["text"] == "弹幕2"
        assert raw_data_list[2].content["text"] == "弹幕3"

    @pytest.mark.asyncio
    async def test_cleanup_with_session(self, bili_config):
        """测试清理资源"""
        provider = BiliDanmakuInputProvider(bili_config)

        # 创建mock session（不需要真正的aiohttp session）
        class MockSession:
            def __init__(self):
                self.closed = False
                self.close_called = False

            async def close(self):
                self.closed = True
                self.close_called = True

        mock_session = MockSession()
        provider._session = mock_session

        await provider._cleanup()

        # 验证close被调用
        assert mock_session.close_called
        assert mock_session.closed
