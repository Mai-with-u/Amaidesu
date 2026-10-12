"""Warudo Provider 测试

注意：大部分需要外部环境的测试已被删除。
本文件保留不需要外部环境的测试。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.tools import ToolInvocation
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
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

        result = await provider.invoke(
            ToolInvocation(
                tool_name="warudo_set_expression",
                arguments={"emotion": "happy", "intensity": 1.0},
            )
        )
        assert result.success is True
        # 情绪写入 blendshape 状态件（监控循环推送），不直连 WebSocket
        assert result.structured_content["applied"]["mouth"] == {"key": "mouth_happy_strong", "weight": 1.0}

    @pytest.mark.asyncio
    async def test_invoke_sight_semantic_target(self, warudo_config, mock_event_bus):
        """set_sight 语义参数：target 三值合法，程度由适配器定（满幅）。"""
        provider = WarudoProvider(warudo_config, event_bus=mock_event_bus)

        result = await provider.invoke(
            ToolInvocation(tool_name="warudo_set_sight", arguments={"target": "camera"})
        )
        assert result.success is True

        bad = await provider.invoke(
            ToolInvocation(tool_name="warudo_set_sight", arguments={"target": "wall"})
        )
        assert bad.success is False

    @pytest.mark.asyncio
    async def test_invoke_returns_failure_when_not_connected(self, warudo_config, mock_event_bus):
        """未连接时 invoke 不抛异常，返回失败 result"""
        provider = WarudoProvider(warudo_config, event_bus=mock_event_bus)
        provider._is_connected = False

        # 关键：不应抛异常
        result = await provider.invoke(
            ToolInvocation(
                tool_name="warudo_trigger_preset_action",
                arguments={"action": "wave"},
            )
        )
        assert result is not None
