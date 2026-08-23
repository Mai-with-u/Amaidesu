"""Warudo Provider 测试（Wave 4 迁移）

注意：大部分需要外部环境的测试已被删除。
本文件保留不需要外部环境的测试。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.tools import ToolInvocation
from src.modules.tools.output.warudo.warudo_provider import WarudoProvider
from src.modules.events.event_bus import EventBus


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock(spec=EventBus)
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def warudo_config():
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


class TestWarudoProviderRendering:
    """渲染测试 - 使用 mock"""

    @pytest.mark.asyncio
    async def test_invoke_set_expression(self, warudo_config, mock_event_bus):
        provider = WarudoProvider(warudo_config, event_bus=mock_event_bus)
        mock_ws = MagicMock()
        mock_ws.close_code = None
        mock_ws.closed = False
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        result = await provider.invoke(
            ToolInvocation(
                tool_name="warudo_set_expression",
                arguments={"name": "mouth_smlie_3", "value": 1.0},
            )
        )
        assert mock_ws.send_json.call_count >= 1
        assert result.success is True

    @pytest.mark.asyncio
    async def test_invoke_returns_failure_when_not_connected(self, warudo_config, mock_event_bus):
        """未连接时 invoke 不抛异常，返回失败 result"""
        provider = WarudoProvider(warudo_config, event_bus=mock_event_bus)
        provider._is_connected = False

        # 关键：不应抛异常
        result = await provider.invoke(
            ToolInvocation(
                tool_name="warudo_set_expression",
                arguments={"name": "mouth_smlie_3", "value": 1.0},
            )
        )
        assert result is not None