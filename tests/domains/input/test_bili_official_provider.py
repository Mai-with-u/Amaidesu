"""
BiliDanmakuOfficialInputProvider 测试
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.domains.input.providers import BiliDanmakuOfficialInputProvider


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

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_success(self, bili_official_config):
        """测试成功处理B站消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock message_handler
        mock_message = MagicMock()
        mock_message.message_info.message_id = "test_msg_123"
        mock_message.message_info.time = 1234567890.0

        provider.message_handler = MagicMock()
        provider.message_handler.create_message_base = AsyncMock(return_value=mock_message)

        # Mock message_cache_service
        provider.message_cache_service = MagicMock()
        provider.message_cache_service.cache_message = MagicMock()

        # 创建队列
        message_queue = asyncio.Queue()

        # 模拟消息数据
        message_data = {"test": "data"}

        # 调用处理方法
        await provider._handle_message_from_bili(message_data, message_queue)

        # 从队列获取结果
        raw_data = await message_queue.get()

        # 验证
        assert raw_data.source == "bili_danmaku_official"
        assert raw_data.data_type == "text"
        assert raw_data.metadata["message_id"] == "test_msg_123"
        assert raw_data.metadata["room_id"] == "test_room_id"

        # 验证消息被缓存
        provider.message_cache_service.cache_message.assert_called_once_with(mock_message)

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_no_message(self, bili_official_config):
        """测试处理返回None的消息"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock message_handler返回None
        provider.message_handler = MagicMock()
        provider.message_handler.create_message_base = AsyncMock(return_value=None)

        provider.message_cache_service = MagicMock()

        # 创建队列
        message_queue = asyncio.Queue()

        # 模拟消息数据
        message_data = {"test": "data"}

        # 调用处理方法
        await provider._handle_message_from_bili(message_data, message_queue)

        # 验证队列中没有数据（使用非阻塞get）
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(message_queue.get(), timeout=0.1)

        # 验证没有缓存消息
        provider.message_cache_service.cache_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_from_bili_exception(self, bili_official_config):
        """测试处理消息时的异常"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock message_handler抛出异常
        provider.message_handler = MagicMock()
        test_exception = RuntimeError("Test error without format strings")
        provider.message_handler.create_message_base = AsyncMock(side_effect=test_exception)

        # 创建队列
        message_queue = asyncio.Queue()

        # 模拟消息数据
        message_data = {"test": "data"}

        # 调用处理方法（应该不抛出异常，只记录错误）
        await provider._handle_message_from_bili(message_data, message_queue)

        # 验证队列中没有数据（使用非阻塞get）
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

        # Mock message_cache_service
        provider.message_cache_service = MagicMock()
        provider.message_cache_service.clear_cache = MagicMock()

        await provider._cleanup()

        # 验证WebSocket被关闭
        mock_ws_client.close.assert_called_once()
        # 验证缓存被清理
        provider.message_cache_service.clear_cache.assert_called_once()
        # 验证websocket_client被设置为None
        assert provider.websocket_client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_websocket_client(self, bili_official_config):
        """测试没有WebSocket客户端时的清理"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock message_cache_service
        provider.message_cache_service = MagicMock()
        provider.message_cache_service.clear_cache = MagicMock()

        await provider._cleanup()

        # 应该不抛出异常
        provider.message_cache_service.clear_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_exception_handling(self, bili_official_config):
        """测试清理时的异常处理"""
        provider = BiliDanmakuOfficialInputProvider(bili_official_config)

        # Mock websocket_client抛出异常
        mock_ws_client = MagicMock()
        mock_ws_client.close = AsyncMock(side_effect=Exception("Close error"))
        provider.websocket_client = mock_ws_client

        # Mock message_cache_service也抛出异常
        provider.message_cache_service = MagicMock()
        provider.message_cache_service.clear_cache = MagicMock(side_effect=Exception("Cache error"))

        # 应该不抛出异常，继续执行
        await provider._cleanup()

        # websocket_client仍应被设置为None（在finally块中）
        assert provider.websocket_client is None
