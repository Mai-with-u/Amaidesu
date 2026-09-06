"""bind_mcp_tools 装配入口单测：报告结构 + 单 server 隔离

mock McpClient/McpToolProvider（不发网络请求）——
- 多个 server 逐个装配，单个失败不影响其它
- 报告结构 {"server": {"ok", "tools", "error"}}
- 配置中 disabled server 跳过
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.registry import ToolRegistry

import src.modules.mcp as mcp_module


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"desc of {name}"
        self.inputSchema = {"type": "object"}


class FakeClient:
    def __init__(self, tools: List[FakeTool], *, connect_ok: bool = True) -> None:
        self.tools = tools
        self.connect_ok = connect_ok
        self.closed = False

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        return self.connect_ok

    async def list_tools(self) -> List[Any]:
        return self.tools if self.connect_ok else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        return None

    async def close(self) -> None:
        self.closed = True


class FakeProvider:
    def __init__(
        self,
        *,
        client: FakeClient,
        server_name: str,
        prefix: Optional[str] = None,
        provider: str = "mcp",
    ) -> None:
        self.client = client
        self.server_name = server_name
        self.prefix = prefix or f"{server_name}_"
        self.provider = provider
        self._specs: List[ToolSpec] = []

    @property
    def name(self) -> str:
        return f"McpProvider:{self.server_name}"

    async def setup(self) -> int:
        if not self.client.connect_ok:
            return 0
        self._specs = [
            ToolSpec(name=f"{self.prefix}{t.name}", description=t.description, provider="mcp")
            for t in self.client.tools
        ]
        return len(self._specs)

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation):
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def close(self) -> None:
        await self.client.close()


@pytest.fixture
def patch_mcp(monkeypatch: pytest.MonkeyPatch) -> Dict[str, FakeClient]:
    """把 mcp 模块的 McpClient/McpToolProvider 替换为假实现。

    测试可通过 ``patch_mcp[name] = FakeClient([...])`` 预注册 server 的工具；
    未预注册的 server 默认暴露 1 个 ping 工具。
    """
    clients: Dict[str, FakeClient] = {}

    def fake_client_cls(name: str, config: Any) -> FakeClient:
        c = clients.get(name)
        if c is None:
            c = FakeClient([FakeTool("ping")])
            clients[name] = c
        return c

    def fake_provider_cls(
        *,
        client: FakeClient,
        server_name: str,
        prefix: Optional[str] = None,
        provider: str = "mcp",
    ) -> FakeProvider:
        return FakeProvider(client=client, server_name=server_name, prefix=prefix, provider=provider)

    monkeypatch.setattr(mcp_module, "McpClient", fake_client_cls)
    monkeypatch.setattr(mcp_module, "McpToolProvider", fake_provider_cls)
    return clients


async def test_bind_two_servers_all_ok(patch_mcp) -> None:
    from src.modules.mcp import bind_mcp_tools

    patch_mcp["serverA"] = FakeClient([FakeTool("perceive"), FakeTool("execute")])
    patch_mcp["other"] = FakeClient([FakeTool("ping")])
    raw_cfg = {
        "servers": {
            "serverA": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:8766/mcp"},
            "other": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:9999/mcp"},
        }
    }

    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, raw_cfg)

    assert report["serverA"]["ok"] is True
    assert report["serverA"]["tools"] == 2
    assert report["other"]["ok"] is True
    assert report["other"]["tools"] == 1
    assert len(registry) == 3  # serverA 2 + other 1

    # 注册进 registry 的工具均带前缀
    names = {s.name for s in registry.list_tools()}
    assert "serverA_perceive" in names
    assert "other_ping" in names


async def test_single_server_failure_isolated(patch_mcp) -> None:
    from src.modules.mcp import bind_mcp_tools

    patch_mcp["broken"] = FakeClient([FakeTool("perceive")], connect_ok=False)
    patch_mcp["good"] = FakeClient([FakeTool("do")])
    raw_cfg = {
        "servers": {
            "broken": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:1/mcp"},
            "good": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:2/mcp"},
        }
    }
    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, raw_cfg)

    assert report["broken"]["ok"] is False
    assert report["broken"]["tools"] == 0
    assert "连接失败" in report["broken"]["error"]
    assert report["good"]["ok"] is True
    assert report["good"]["tools"] == 1
    assert len(registry) == 1  # 只有 good 注册了工具


async def test_disabled_server_skipped(patch_mcp) -> None:
    from src.modules.mcp import bind_mcp_tools

    raw_cfg = {
        "servers": {
            "enabled_one": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:3/mcp"},
            "disabled_one": {"enabled": False, "transport": "http", "url": "http://127.0.0.1:4/mcp"},
        }
    }
    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, raw_cfg)

    assert "disabled_one" not in report  # disabled 不装配也不报告
    assert report["enabled_one"]["ok"] is True


async def test_empty_config_no_servers(patch_mcp) -> None:
    from src.modules.mcp import bind_mcp_tools

    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, None)  # None → 空配置
    assert report == {}
    assert len(registry) == 0


async def test_duplicate_prefix_collision_reported(patch_mcp) -> None:
    """两个 server 若前缀相同：先注册保留，后注册 tools=0 并带 error 说明。"""
    from src.modules.mcp import bind_mcp_tools

    patch_mcp["first"] = FakeClient([FakeTool("ping")])
    patch_mcp["second"] = FakeClient([FakeTool("ping")])
    raw_cfg = {
        "servers": {
            "first": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:5/mcp", "prefix": "same_"},
            "second": {"enabled": True, "transport": "http", "url": "http://127.0.0.1:6/mcp", "prefix": "same_"},
        }
    }

    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, raw_cfg)

    assert report["first"]["ok"] is True
    assert report["first"]["tools"] == 1
    assert report["second"]["ok"] is True
    assert report["second"]["tools"] == 0  # 重名被跳过
    assert "占用" in report["second"]["error"]
