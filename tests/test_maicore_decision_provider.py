"""
MaiCoreDecisionProvider单元测试
"""

import pytest
from unittest.mock import Mock, AsyncMock

from src.core.providers.maicore_decision_provider import MaiCoreDecisionProvider
from src.canonical.canonical_message import CanonicalMessage


@pytest.fixture
def event_bus_mock():
    """EventBus mock"""
    event_bus = Mock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def maicore_config():
    """MaiCoreDecisionProvider配置"""
    return {
        "host": "localhost",
        "port": 8000,
        "platform": "test_platform",
        "http_host": "localhost",
        "http_port": 8080,
        "http_callback_path": "/test_callback",
    }


@pytest.mark.asyncio
async def test_maicore_provider_setup(event_bus_mock, maicore_config):
    """测试Provider初始化"""
    provider = MaiCoreDecisionProvider(maicore_config)

    await provider.setup(event_bus_mock, maicore_config)

    assert provider.platform == "test_platform"
    assert provider.host == "localhost"
    assert provider.port == 8000
    assert provider.http_host == "localhost"
    assert provider.http_port == 8080


@pytest.mark.asyncio
async def test_maicore_provider_connect_disconnect(event_bus_mock, maicore_config):
    """测试连接和断开"""
    provider = MaiCoreDecisionProvider(maicore_config)

    await provider.setup(event_bus_mock, maicore_config)
    await provider.connect()

    assert provider._is_connected == True

    await provider.disconnect()
    assert provider._is_connected == False


@pytest.mark.asyncio
async def test_maicore_provider_decide(event_bus_mock, maicore_config):
    """测试决策功能"""
    provider = MaiCoreDecisionProvider(maicore_config)

    await provider.setup(event_bus_mock, maicore_config)

    # 创建CanonicalMessage
    canonical_message = CanonicalMessage(
        text="测试消息",
        source="test",
        metadata={"user_id": "test_user"},
    )

    # Mock Router
    provider._router = Mock()
    provider._router.send_message = AsyncMock()

    # 调用decide
    result = await provider.decide(canonical_message)

    # 验证
    assert result is not None
    assert result.message_info.sender.user_id == "test_user"
    provider._router.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_maicore_provider_http_handler_registration(event_bus_mock, maicore_config):
    """测试HTTP处理器注册"""
    provider = MaiCoreDecisionProvider(maicore_config)

    # Mock handler
    async def mock_handler(request):
        from aiohttp import web

        return web.json_response({"status": "ok"})

    await provider.setup(event_bus_mock, maicore_config)
    provider.register_http_handler("test_key", mock_handler)

    assert "test_key" in provider._http_request_handlers
    assert mock_handler in provider._http_request_handlers["test_key"]


@pytest.mark.asyncio
async def test_maicore_provider_cleanup(event_bus_mock, maicore_config):
    """测试清理资源"""
    provider = MaiCoreDecisionProvider(maicore_config)

    await provider.setup(event_bus_mock, maicore_config)
    await provider.connect()

    await provider.cleanup()

    assert provider._is_connected == False
    assert provider._router is None


def test_maicore_provider_get_info(maicore_config):
    """测试获取Provider信息"""
    provider = MaiCoreDecisionProvider(maicore_config)

    info = provider.get_info()

    assert info["name"] == "MaiCoreDecisionProvider"
    assert info["version"] == "1.0.0"
    assert info["host"] == "localhost"
    assert info["port"] == 8000
    assert info["platform"] == "test_platform"
