"""
MCPServerService 单元测试

测试 MCP Server 服务的核心功能：
- send_intent 工具的各种调用场景
- 参数验证
- 错误处理（超时、API 错误）
- 边界条件

运行: uv run pytest tests/modules/mcp/test_mcp_server.py -v
"""

import pytest

from src.modules.mcp import MCPServerService


@pytest.fixture
async def mcp_service():
    """创建 MCPServerService 实例"""
    service = MCPServerService()
    await service.setup({})
    yield service
    await service.cleanup()


# =============================================================================
# send_intent 成功场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_send_intent_valid_params(mcp_service: MCPServerService):
    """测试 send_intent 使用有效参数调用 - 应成功"""
    result = await mcp_service.send_intent(
        action="wave hand",
        emotion="happy",
        speech="Hello, how are you?",
        context="user greeting",
        priority=50,
    )
    # 验证返回结果
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_intent_minimal_params(mcp_service: MCPServerService):
    """测试 send_intent 仅提供必需参数 - 应成功"""
    result = await mcp_service.send_intent(action="nod")
    # 仅提供 action 参数时应成功
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_send_intent_empty_speech(mcp_service: MCPServerService):
    """测试 send_intent speech=None 时的行为 - 应成功"""
    # speech 为 None 应该被允许
    result = await mcp_service.send_intent(
        action="dance",
        emotion="excited",
        speech=None,
        priority=50,
    )
    assert result is not None
    assert isinstance(result, str)


# =============================================================================
# send_intent 错误场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_send_intent_api_timeout(mcp_service: MCPServerService):
    """测试 send_intent API 超时 - 应抛出 MCPError

    Note: 当前测试失败是预期的 (TDD Red)，因为 send_intent 尚未实现。
    1.4 实现时需要创建 src.modules.mcp.exceptions 模块并定义 MCPError
    """
    # 测试会在调用 send_intent 时失败，因为该方法不存在
    # 1.4 实现时需 mock httpx 客户端模拟超时行为
    await mcp_service.send_intent(
        action="wave",
        emotion="happy",
        speech="Hi",
        priority=50,
    )
    pytest.fail("send_intent 未实现，不应到达此处")


@pytest.mark.asyncio
async def test_send_intent_api_error(mcp_service: MCPServerService):
    """测试 send_intent API 返回错误 - 应抛出 MCPError

    Note: 当前测试失败是预期的 (TDD Red)，因为 send_intent 尚未实现。
    1.4 实现时需要创建 src.modules.mcp.exceptions 模块并定义 MCPError
    """
    # 测试会在调用 send_intent 时失败，因为该方法不存在
    # 1.4 实现时需 mock httpx 客户端模拟 API 错误行为
    await mcp_service.send_intent(
        action="wave",
        emotion="happy",
        speech="Hi",
        priority=50,
    )
    pytest.fail("send_intent 未实现，不应到达此处")


# =============================================================================
# send_intent 参数验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_priority_out_of_range(mcp_service: MCPServerService):
    """测试 priority 参数超出有效范围 (0-100) - 应抛出 ValueError"""
    # priority < 0 应抛出 ValueError
    with pytest.raises(ValueError, match="priority.*0.*100|0.*100.*priority"):
        await mcp_service.send_intent(
            action="wave",
            emotion="happy",
            speech="Hi",
            priority=-1,
        )

    # priority > 100 应抛出 ValueError
    with pytest.raises(ValueError, match="priority.*0.*100|0.*100.*priority"):
        await mcp_service.send_intent(
            action="wave",
            emotion="happy",
            speech="Hi",
            priority=101,
        )


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
