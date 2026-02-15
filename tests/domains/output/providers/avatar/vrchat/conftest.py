"""VRChat Provider 测试共享 fixtures"""

from unittest.mock import MagicMock

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
def vrchat_config():
    """VRChat Provider 配置"""
    return {"vrc_host": "127.0.0.1", "vrc_out_port": 9000}


@pytest.fixture
def mock_osc_client():
    """创建一个 mock OSC 客户端"""
    client = MagicMock()
    client.send_message = MagicMock()
    return client


@pytest.fixture
def mock_logger():
    """创建 mock logger"""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger
