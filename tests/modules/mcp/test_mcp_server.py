"""
MCPServerService 单元测试

测试 MCP Server 服务的核心功能（对齐 Plugin 工具能力）：
- send_action 工具：触发 Avatar 动作 + 情绪 + 文本
- get_status 工具：查询 Amaidesu 运行状态
- 参数验证（priority 0-100 范围）
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
        action_type="wave hand",
        action_value="user greeting",
        emotion="happy",
        priority=50,
        text="Hello, how are you?",
    )
    # 验证返回结果
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_action_minimal_params(mcp_service: MCPServerService):
    """测试 send_action 仅提供必需参数 - 应成功

    新签名要求 action_type / action_value / emotion 必填，priority 与 text 有默认值。
    此处仅验证最少必填参数组合。
    """
    result = await mcp_service.send_action(
        action_type="nod", action_value="nod", emotion="neutral"
    )
    # 仅提供 action 参数时应成功
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_action_no_text(mcp_service: MCPServerService):
    """测试 send_action text=None 时的行为 - 应成功"""
    # text 为 None 应该被允许
    result = await mcp_service.send_action(
        action_type="dance",
        action_value="dance",
        emotion="excited",
        priority=50,
        text=None,
    )
    assert result is not None
    assert isinstance(result, str)


# =============================================================================
# send_action 错误场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_send_action_api_timeout(mcp_service: MCPServerService):
    """测试 send_action API 超时 - 应抛出 Exception"""
    import httpx
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Connection timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.modules.mcp.service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(Exception, match="timed out"):
            await mcp_service.send_action(
                action_type="wave",
                action_value="wave",
                emotion="happy",
                priority=50,
                text="Hi",
            )


@pytest.mark.asyncio
async def test_send_action_api_error(mcp_service: MCPServerService):
    """测试 send_action API 返回错误 - 应抛出 Exception"""
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
        with pytest.raises(Exception, match="MCP API error"):
            await mcp_service.send_action(
                action_type="wave",
                action_value="wave",
                emotion="happy",
                priority=50,
                text="Hi",
            )


# =============================================================================
# send_action 参数验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_priority_out_of_range(mcp_service: MCPServerService):
    """测试 priority 参数超出有效范围 (0-100) - 应抛出 ValueError"""
    # priority < 0 应抛出 ValueError
    with pytest.raises(ValueError, match="priority.*0.*100|0.*100.*priority"):
        await mcp_service.send_action(
            action_type="wave",
            action_value="wave",
            emotion="happy",
            text="Hi",
            priority=-1,
        )

    # priority > 100 应抛出 ValueError
    with pytest.raises(ValueError, match="priority.*0.*100|0.*100.*priority"):
        await mcp_service.send_action(
            action_type="wave",
            action_value="wave",
            emotion="happy",
            text="Hi",
            priority=101,
        )


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
