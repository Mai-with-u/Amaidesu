"""MCPServerService 单元测试

测试 MCP Server 服务的核心功能（对齐 Plugin 工具能力）：
- send_action 工具：触发 Avatar 动作 + 情绪 + 文本
- get_status 工具：查询 Amaidesu 运行状态
- 错误处理（httpx 超时、API 错误响应）

运行: uv run pytest tests/modules/mcp/test_mcp_server.py -v
"""

import pytest

from src.modules.mcp import MCPServerService
from src.modules.mcp.config import MCPServerConfig


@pytest.fixture
async def mcp_service():
    """创建 MCPServerService 实例（typed config 注入）"""
    service = MCPServerService(MCPServerConfig())
    await service.setup({"enabled": False})
    yield service
    await service.cleanup()


# =============================================================================
# send_action 成功场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_send_action_valid_params(mcp_service: MCPServerService):
    """测试 send_action 使用有效参数调用 - 应成功"""
    result = await mcp_service.send_action(
        action_name="wave hand",
        action_parameters={"value": "user greeting"},
        emotion_name="happy",
        emotion_intensity=0.8,
        text="Hello, how are you?",
    )
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_action_minimal_params(mcp_service: MCPServerService):
    """测试 send_action 仅提供最少有效参数 - 应成功

    新签名要求至少提供 action_name / emotion_name / text 之一。
    """
    result = await mcp_service.send_action(action_name="nod", emotion_name="neutral")
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_action_no_text(mcp_service: MCPServerService):
    """测试 send_action text=None 时的行为 - 应成功"""
    result = await mcp_service.send_action(
        action_name="dance",
        emotion_name="excited",
        text=None,
    )
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_action_only_text(mcp_service: MCPServerService):
    """测试 send_action 仅提供 text 参数 - 应成功"""
    result = await mcp_service.send_action(text="Hello everyone!")
    assert result is not None
    assert isinstance(result, str)


# =============================================================================
# send_action 错误场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_send_action_api_timeout(mcp_service: MCPServerService):
    """测试 send_action API 超时 - 应返回包含 timed out 的错误字符串"""
    import httpx
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Connection timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.modules.mcp.service.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_service.send_action(
            action_name="wave",
            emotion_name="happy",
            text="Hi",
        )

    assert isinstance(result, str)
    assert "timed out" in result.lower()


@pytest.mark.asyncio
async def test_send_action_api_error(mcp_service: MCPServerService):
    """测试 send_action API 返回错误 - 应返回包含 MCP API error 的错误字符串"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.json.return_value = {"error": "Internal server error"}
    mock_response.content = b'{"error": "Internal server error"}'
    mock_response.text = '{"error": "Internal server error"}'

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.modules.mcp.service.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_service.send_action(
            action_name="wave",
            emotion_name="happy",
            text="Hi",
        )

    assert isinstance(result, str)
    assert "MCP API error" in result


# =============================================================================
# get_status 工具测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_status(mcp_service: MCPServerService):
    """测试 get_status 状态查询 - 应返回包含 'running' 字段的字符串"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"running": True, "version": "1.0.0"}
    mock_response.content = b'{"running": true, "version": "1.0.0"}'
    mock_response.text = '{"running": true, "version": "1.0.0"}'

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.modules.mcp.service.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_service.get_status()

    assert result is not None
    assert isinstance(result, str)
    assert "running" in result


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
