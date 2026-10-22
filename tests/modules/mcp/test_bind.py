"""bind_mcp_tools 装配入口（报告结构、单 server 隔离、同名工具不冲突、disabled 跳过）与 MCP 配置校验"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

import src.modules.mcp as mcp_module
from src.modules.mcp import bind_mcp_tools, close_mcp_providers
from src.modules.mcp.config import McpExternalConfig, McpServerConfig
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from tests.modules.mcp.fakes import AnyConfig, FakeMcpClient


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"desc of {name}"
        self.inputSchema = {"type": "object"}


class FakeClient:
    def __init__(self, tools: List[FakeTool], *, connect_ok: bool = True) -> None:
        self.tools = tools
        self.connect_ok = connect_ok

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        return self.connect_ok

    async def list_tools(self) -> List[Any]:
        return self.tools if self.connect_ok else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        return None

    async def close(self) -> None:
        return None


class FakeProvider:
    def __init__(self, *, client: FakeClient, server_name: str, provider: Optional[str] = None) -> None:
        self.client = client
        self.server_name = server_name
        self.provider = provider or server_name
        self._specs: List[ToolSpec] = []
        self.on_tools_refreshed: Optional[Any] = None
        self.recovery_started = False

    def start_recovery(self) -> None:
        self.recovery_started = True

    @property
    def name(self) -> str:
        return self.provider  # 与真实 McpToolProvider 对齐：单名 = provider（默认 server 名）

    async def setup(self) -> int:
        if not self.client.connect_ok:
            return 0
        # 声明名 = server 原始名，全名 = <provider>_<原始名>
        self._specs = [
            ToolSpec(name=t.name, description=t.description, provider=self.provider) for t in self.client.tools
        ]
        return len(self._specs)

    def list_tools(self):
        return list(self._specs)

    async def invoke(self, invocation):
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def close(self) -> None:
        await self.client.close()


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> Dict[str, FakeClient]:
    """把 mcp 模块的 McpClient/McpToolProvider 换成假实现；按 server 名预注册，未注册的默认暴露一个 ping 工具。"""
    registered: Dict[str, FakeClient] = {}

    def fake_client(name: str, config: Any) -> FakeClient:
        return registered.setdefault(name, FakeClient([FakeTool("ping")]))

    monkeypatch.setattr(mcp_module, "McpClient", fake_client)
    monkeypatch.setattr(
        mcp_module,
        "McpToolProvider",
        lambda *, client, server_name, provider=None: FakeProvider(
            client=client, server_name=server_name, provider=provider
        ),
    )
    return registered


def _servers(**enabled_by_name: bool) -> dict:
    return {
        "servers": {
            name: {"enabled": enabled, "transport": "http", "url": f"http://127.0.0.1:{i + 1}/mcp"}
            for i, (name, enabled) in enumerate(enabled_by_name.items())
        }
    }


async def test_servers_bind_independently_with_report_and_derived_full_names(clients) -> None:
    clients["serverA"] = FakeClient([FakeTool("perceive"), FakeTool("execute")])
    clients["broken"] = FakeClient([FakeTool("perceive")], connect_ok=False)
    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, _servers(serverA=True, broken=True, other=True, off=False))

    assert report["serverA"]["ok"] is True and report["serverA"]["tools"] == 2
    assert report["other"]["ok"] is True and report["other"]["tools"] == 1
    assert (
        report["broken"]["ok"] is False and report["broken"]["tools"] == 0 and "连接失败" in report["broken"]["error"]
    )
    assert report["broken"]["degraded"] is True and report["serverA"]["degraded"] is False
    assert "off" not in report  # disabled 不装配也不报告
    assert {s.full_name for s in registry.list_tools()} == {
        "serverA_perceive",
        "serverA_execute",
        "other_ping",
    }  # 单个失败不影响其它
    # 连不上的 server 占位登记并起后台重试；连上的不起
    providers = {p.name: p for p in registry._providers}
    assert set(providers) == {"serverA", "broken", "other"}
    assert providers["broken"].recovery_started and not providers["serverA"].recovery_started


async def test_same_raw_tool_name_on_two_servers_does_not_collide(clients) -> None:
    clients["first"] = FakeClient([FakeTool("ping")])
    clients["second"] = FakeClient([FakeTool("ping")])
    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, _servers(first=True, second=True))
    assert report["first"]["ok"] and report["second"]["ok"]
    assert {s.full_name for s in registry.list_tools()} == {"first_ping", "second_ping"}  # 全名带各自 provider 前缀


async def test_late_server_tools_are_registered_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """server 晚于本进程启动：先 0 工具占位登记，连上后经 on_tools_refreshed 补注册，主播随即可见。"""
    created: Dict[str, FakeMcpClient] = {}

    def offline_client(name: str, config: Any) -> FakeMcpClient:
        client = FakeMcpClient(name=name, config=AnyConfig())
        client._connected = False
        created[name] = client
        return client

    monkeypatch.setattr(mcp_module, "McpClient", offline_client)
    registry = ToolRegistry()
    report = await bind_mcp_tools(registry, _servers(late=True))
    try:
        assert report["late"]["degraded"] is True and registry.list_tools(for_agent="streamer") == []
        prov = next(p for p in registry._providers if p.name == "late")
        assert prov.recovering

        # 模拟 server 上线后的一轮恢复（真实恢复循环按 5s 起步退避，同一出口）
        client = created["late"]
        client.set_tools([FakeTool("read_group"), FakeTool("send_group")])
        client.next_connect_result = True
        assert await prov.setup() == 2
        assert {s.full_name for s in registry.list_tools(for_agent="streamer")} == {
            "late_read_group",
            "late_send_group",
        }
        result = await registry.invoke(ToolInvocation(tool_name="late_send_group", arguments={"text": "hi"}))
        assert result.success and client.called == [{"name": "send_group", "arguments": {"text": "hi"}}]
    finally:
        await close_mcp_providers(registry)
    assert not prov.recovering  # 停机时恢复循环随连接一并取消


async def test_empty_config_binds_nothing(clients) -> None:
    registry = ToolRegistry()
    assert await bind_mcp_tools(registry, None) == {} and len(registry) == 0


def test_server_config_validation() -> None:
    http = McpServerConfig(transport="http", url="http://127.0.0.1:8766/mcp")
    assert http.url == "http://127.0.0.1:8766/mcp" and http.enabled is True
    stdio = McpServerConfig(transport="stdio", command="npx", args=["-y", "server"])
    assert (stdio.transport, stdio.command) == ("stdio", "npx")
    assert http.timeout_seconds >= 1.0 and http.request_timeout_ms == 90_000  # 默认请求期限容纳主动等待

    for bad in (
        {"transport": "http", "url": "127.0.0.1:8766"},  # url 缺协议
        {"transport": "http", "url": "http://x/mcp", "unknown_key": 1},  # extra=forbid
        {"request_timeout_ms": 0},
    ):
        with pytest.raises(ValidationError):
            McpServerConfig(**bad)


def test_external_config_parsing_is_tolerant() -> None:
    cfg = McpExternalConfig.parse_extra(
        {
            "servers": {
                "serverA": {"transport": "http", "url": "http://127.0.0.1:8766/mcp"},
                "off": {"enabled": False, "url": "http://b/mcp"},
                "bad_server": {"transport": "stdio", "command": "npx", "args": ["-y", "mcp"]},
            },
            "future_key": 1,  # extra=allow：未知键不抛，为未来扩展容错
        }
    )
    assert len(cfg.servers) == 3 and cfg.enabled_servers() == ["serverA", "bad_server"]
    assert McpExternalConfig.parse_extra(None).servers == {} and McpExternalConfig.parse_extra("garbage").servers == {}
    assert McpExternalConfig.parse_extra({}).enabled_servers() == []
