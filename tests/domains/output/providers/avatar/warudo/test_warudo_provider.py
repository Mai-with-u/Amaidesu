"""Warudo Provider 测试

注意：大部分需要外部环境的测试已被删除。
本文件保留不需要外部环境的测试。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.types import Intent, EmotionType
from src.domains.output.providers.avatar.warudo.warudo_provider import WarudoOutputProvider
from src.modules.di.context import ProviderContext


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


@pytest.fixture
def mock_provider_context(mock_event_bus):
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=mock_event_bus,
        config_service=MagicMock(),
    )


@pytest.fixture
def warudo_config():
    return {"ws_host": "localhost", "ws_port": 19190}


@pytest.fixture
def mock_websocket():
    """创建一个 mock WebSocket 连接"""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


# =============================================================================
# Tests - 不需要外部环境
# =============================================================================


class TestWarudoProviderRendering:
    """渲染测试 - 使用 mock"""

    @pytest.mark.asyncio
    async def testexecute_with_expressions(self, warudo_config, mock_provider_context):
        provider = WarudoOutputProvider(warudo_config, context=mock_provider_context)
        # 创建 AsyncMock 的 websocket
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        # 使用 Intent 调用基类的 execute
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        await provider.execute(intent)

        assert mock_ws.send_json.call_count >= 1

    @pytest.mark.asyncio
    async def testexecute_when_not_connected(self, warudo_config, mock_provider_context):
        provider = WarudoOutputProvider(warudo_config, context=mock_provider_context)
        provider._is_connected = False

        # 使用 Intent 调用基类的 execute
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        # 应该不抛出异常，只是跳过渲染
        await provider.execute(intent)
