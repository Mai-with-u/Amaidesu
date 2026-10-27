"""stop 资源清理契约：minecraft 摘除 provider 并关闭 MCP 客户端、streamer 摘除 reply/rundown provider、enable/disable 循环不泄漏"""

from __future__ import annotations

from typing import Any, Iterable, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.agents.base import BaseAgent
from src.modules.agents.manager import AgentManager
from src.modules.mcp.config import McpServerConfig
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


class _TagProvider(BaseToolProvider):
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
    """start 时经基类入口注册一个 provider、stop 时摘除的 stub Agent。"""

    name = "stub_leaky"
    description = "stop 清理契约测试 stub"

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        super().__init__()
        self._registry = registry

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    async def _on_start(self) -> None:
        self.register_tool_provider(_TagProvider("stub_leaky"), registry=self._registry)

    async def _on_stop(self) -> None:
        self.unregister_tool_providers()


async def test_minecraft_stop_removes_its_provider_and_closes_the_mcp_client_exactly_once() -> None:
    registry = ToolRegistry()
    agent = MinecraftAgent(MinecraftConfig(mcp=McpServerConfig(enabled=True)), tool_registry=registry)
    client = MagicMock(close=AsyncMock())
    provider = MagicMock(setup=AsyncMock(return_value=1))
    provider.name = "maicraft"
    # 替身 spec 自带 minecraft 名单（真实 MaicraftMcpProvider.list_tools 的盖章语义）
    provider.list_tools = lambda: [
        ToolSpec(name="greet", description="g", provider="maicraft", visible_to=["minecraft"])
    ]

    with (
        patch("src.modules.mcp.client.McpClient", return_value=client),
        patch("src.agents.minecraft.agent.MaicraftMcpProvider", return_value=provider),
    ):
        await agent.start()
    assert registry.has("maicraft_greet") and registry.visible_to_of("maicraft_greet") == ["minecraft"]
    assert client.close.await_count == 0

    await agent.stop()
    assert not registry.has("maicraft_greet") and registry._providers == [] and client.close.await_count == 1


async def test_streamer_stop_removes_reply_and_rundown_providers() -> None:
    registry = ToolRegistry()
    agent = StreamerAgent(
        StreamerConfig(), llm_manager=None, prompt_manager=None, event_bus=None, tool_registry=registry
    )
    agent._register_tools()
    assert registry.has("streamer_reply") and registry.has("rundown_control")

    reply_provider = agent._reply_provider
    await agent._on_stop()
    assert not registry.has("streamer_reply") and not registry.has("rundown_control")
    assert reply_provider not in registry._providers and registry._providers == []


async def test_enable_disable_loop_does_not_leak_providers() -> None:
    registry = ToolRegistry()
    manager = AgentManager(tool_registry=registry)

    def fake_instantiate(name: str, config: Any, **kwargs: Any) -> BaseAgent:
        return _SelfRegisteringAgent(registry=registry)

    with patch("src.modules.agents.factory.instantiate_agent", side_effect=fake_instantiate):
        for _ in range(2):
            assert await manager.enable_agent("stub_leaky", config={}) is True and registry.has("stub_leaky_ping")
            assert await manager.disable_agent("stub_leaky") is True and not registry.has("stub_leaky_ping")
    assert registry._providers == [] and len(registry) == 0
