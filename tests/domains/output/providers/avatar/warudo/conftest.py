"""Warudo Provider 测试共享 fixtures"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.di.context import ProviderContext


@pytest.fixture
def mock_provider_context():
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=MagicMock(),
        config_service=MagicMock(),
    )


@pytest.fixture
def warudo_config():
    """Warudo Provider 配置"""
    return {"ws_host": "localhost", "ws_port": 19190}


@pytest.fixture
def mock_websocket():
    """创建一个 mock WebSocket 连接"""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.closed = False
    return ws


@pytest.fixture
def mock_logger():
    """创建 mock logger"""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger
