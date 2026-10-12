"""stop 资源清理契约测试（P2）。

覆盖：
- minecraft ``stop()`` → 本 Agent provider 摘除 + MCP 客户端 ``close()`` 被调
  （mock 计数 = 1）
- streamer ``stop()`` → reply / rundown provider 摘除
- 循环 enable/disable 2 次 → registry provider/工具总数不增长
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Iterable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


# =============================================================================
# 测试用件
# =============================================================================


class _TagProvider(BaseToolProvider):
    """可打标的测试 Provider（断言 registry 摘除用对象引用比对）。"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def list_tools(self) -> Iterable[ToolSpec]:
        return [ToolSpec(name="ping", description="p", provider=self._name)]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


class _SelfRegisteringAgent(BaseAgent):
    """start 时经基类入口注册一个 provider 的 stub Agent（enable/disable 循环用）。"""

    name = "stub_leaky"
    description = "stop 清理契约测试 stub"

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        super().__init__()
        self._registry = registry
        self._provider: Any = None

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    async def _on_start(self) -> None:
        self._provider = _TagProvider("stub_leaky")
        self.register_tool_provider(self._provider, registry=self._registry)

    async def _on_stop(self) -> None:
        self.unregister_tool_providers()


@pytest.fixture
async def manager() -> AsyncGenerator[tuple[AgentManager, ToolRegistry], None]:
    """AgentManager + 独立 ToolRegistry；工厂 patch 为返回自注册 stub。"""
    registry = ToolRegistry()
    mgr = AgentManager(tool_registry=registry)

    def _fake_instantiate(name: str, config: Any, **kwargs: Any) -> BaseAgent:
        return _SelfRegisteringAgent(registry=registry)

    with patch("src.modules.agents.factory.instantiate_agent", side_effect=_fake_instantiate):
        yield mgr, registry


# =============================================================================
# minecraft stop → provider 摘除 + MCP close
# =============================================================================


@pytest.mark.asyncio
async def test_minecraft_stop_unregisters_providers_and_closes_mcp() -> None:
    """minecraft start 装配 MCP 后 stop：maicraft 工具摘除、MCP close 恰好一次。"""
    from src.agents.minecraft.agent import MinecraftAgent
    from src.agents.minecraft.config import MinecraftConfig
    from src.modules.mcp.config import McpServerConfig

    registry = ToolRegistry()
    agent = MinecraftAgent(
        MinecraftConfig(mcp=McpServerConfig(enabled=True)),
        tool_registry=registry,
    )

    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_prov = MagicMock()
    mock_prov.setup = AsyncMock(return_value=1)
    mock_prov.name = "maicraft"
    mock_prov.list_tools = lambda: [ToolSpec(name="greet", description="g", provider="maicraft")]

    with (
        patch("src.modules.mcp.client.McpClient", return_value=mock_client),
        patch("src.modules.mcp.provider.McpToolProvider", return_value=mock_prov),
    ):
        await agent.start()

    # 装配成功：maicraft_greet 已注册且归属 minecraft 名单
    assert registry.has("maicraft_greet")
    assert registry.visible_to_of("maicraft_greet") == ["minecraft"]
    assert mock_client.close.await_count == 0

    await agent.stop()

    assert not registry.has("maicraft_greet")
    assert registry._providers == []
    assert mock_client.close.await_count == 1


# =============================================================================
# streamer stop → reply / rundown provider 摘除
# =============================================================================


@pytest.mark.asyncio
async def test_streamer_stop_unregisters_reply_and_rundown() -> None:
    """streamer 注册 reply/rundown 后 stop：两个 provider 均被摘除。"""
    from src.agents.streamer.config import StreamerConfig
    from src.agents.streamer.streamer_agent import StreamerAgent

    registry = ToolRegistry()
    agent = StreamerAgent(
        StreamerConfig(),
        llm_manager=None,
        prompt_manager=None,
        event_bus=None,
        tool_registry=registry,
    )

    agent._register_tools()
    assert registry.has("streamer_reply") and registry.has("rundown_control")

    reply_provider = agent._reply_provider
    await agent._on_stop()

    assert not registry.has("streamer_reply")
    assert not registry.has("rundown_control")
    assert reply_provider not in registry._providers
    assert registry._providers == []


# =============================================================================
# enable/disable 循环无泄漏
# =============================================================================


@pytest.mark.asyncio
async def test_enable_disable_loop_does_not_leak_providers(manager) -> None:
    """enable → disable 循环 2 次：registry 的 provider 与工具总数不增长。"""
    mgr, registry = manager

    for _ in range(2):
        ok = await mgr.enable_agent("stub_leaky", config={})
        assert ok is True
        assert registry.has("stub_leaky_ping")
        ok = await mgr.disable_agent("stub_leaky")
        assert ok is True
        assert not registry.has("stub_leaky_ping")

    assert registry._providers == []
    assert len(registry) == 0
