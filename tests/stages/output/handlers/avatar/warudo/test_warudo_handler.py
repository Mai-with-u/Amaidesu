"""Warudo Handler 测试

注意：大部分需要外部环境的测试已被删除。
本文件保留不需要外部环境的测试。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.types import Intent, IntentMetadata
from src.stages.output.handlers.avatar.warudo.warudo_handler import WarudoHandler
from src.modules.events.event_bus import EventBus


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock(spec=EventBus)
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


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


class TestWarudoHandlerRendering:
    """渲染测试 - 使用 mock"""

    @pytest.mark.asyncio
    async def test_handle_with_expressions(self, warudo_config, mock_event_bus):
        handler = WarudoHandler(warudo_config, event_bus=mock_event_bus)
        # 创建 AsyncMock 的 websocket
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        handler.websocket = mock_ws
        handler._is_connected = True

        # 使用 Intent 调用 handle
        intent = Intent(
            metadata=IntentMetadata(source_id="test", decision_time=1234567890),
            emotion="happy",
            speech="测试",
        )
        await handler.handle(intent)

        assert mock_ws.send_json.call_count >= 1

    @pytest.mark.asyncio
    async def test_handle_when_not_connected(self, warudo_config, mock_event_bus):
        handler = WarudoHandler(warudo_config, event_bus=mock_event_bus)
        handler._is_connected = False

        # 使用 Intent 调用 handle
        intent = Intent(
            metadata=IntentMetadata(source_id="test", decision_time=1234567890),
            emotion="happy",
            speech="测试",
        )
        # 应该不抛出异常，只是跳过渲染
        await handler.handle(intent)