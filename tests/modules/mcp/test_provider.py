"""McpToolProvider：工具缓存与调用转发、连接/重连、健康转发、适配器声明（任务查询与资源订阅）、业务拒绝不熔断"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools import ToolInvocation, ToolRegistry, ToolSpec
from tests.modules.mcp.fakes import AnyConfig, FakeMcpClient, FakeResult, FakeTool


def _provider(server: str = "serverA", provider: Optional[str] = None) -> tuple[FakeMcpClient, McpToolProvider]:
    client = FakeMcpClient(name=server, config=AnyConfig())
    return client, McpToolProvider(client=client, server_name=server, provider=provider)


async def test_setup_caches_specs_with_raw_names_and_derived_full_names() -> None:
    client, prov = _provider()
    client.set_tools([FakeTool("perceive", "观察"), FakeTool("execute", "执行", input_schema={"type": "object"})])
    assert await prov.setup() == 2
    specs = list(prov.list_tools())
    assert [s.name for s in specs] == ["perceive", "execute"]  # 声明名 = server 原始名
    assert [s.full_name for s in specs] == ["serverA_perceive", "serverA_execute"]  # 全名派生 <provider>_<原始名>
    assert specs[0].provider == "serverA" and specs[1].parameters_schema == {"type": "object"}
    assert prov.name == "serverA"

    client._connected = False
    broken_client, broken = _provider()
    broken_client._connected = False
    assert await broken.setup() == 0 and list(broken.list_tools()) == []  # 连不上：降级为 0 工具

    overridden_client, overridden = _provider(provider="game")
    overridden_client.set_tools([FakeTool("perceive", "观察")])
    await overridden.setup()
    assert overridden.name == "game" and list(overridden.list_tools())[0].full_name == "game_perceive"


async def test_invoke_calls_server_with_raw_name_and_fails_softly() -> None:
    client, prov = _provider()
    client.set_tools([FakeTool("perceive", "观察"), FakeTool("maicraft_task", "")])  # server 原名自带前缀的回归形态
    assert (
        await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
    ).success is False  # 未 setup：不属于本 Provider
    await prov.setup()

    result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive", arguments={"view": "situation"}))
    assert result.success is True and result.structured_content == {"raw": "perceive"}
    await prov.invoke(ToolInvocation(tool_name="serverA_maicraft_task"))
    assert client.called == [
        {"name": "perceive", "arguments": {"view": "situation"}},
        {"name": "maicraft_task", "arguments": {}},  # 声明名原样保留，不做前缀拼接/解析
    ]

    unknown = await prov.invoke(ToolInvocation(tool_name="serverA_nonexistent"))
    assert unknown.success is False and "不属于" in unknown.error_message and len(client.called) == 2

    client._connected = False  # 模拟断连 → call_tool 返回 None
    dropped = await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
    assert dropped.success is False and "失败" in dropped.error_message


async def test_connect_disconnect_and_reconnect_delegate_to_client() -> None:
    client, prov = _provider()
    assert prov.supports_reconnect is True
    assert await prov.connect() is True and client.connect_calls == 0  # 已连接短路，不穿透

    client._connected = False
    client.next_connect_result = True
    assert await prov.connect() is True and client.connect_calls == 1
    client._connected = False
    client.next_connect_result = False
    assert await prov.connect() is False  # 底层失败透传

    assert await prov.disconnect() is True and await prov.disconnect() is True  # 幂等，每次都委托 close
    assert client.close_calls == 2


async def test_reconnect_rebuilds_channel_resyncs_specs_and_notifies() -> None:
    client, prov = _provider()
    client.set_tools([FakeTool("t1"), FakeTool("t2")])
    client.next_connect_result = True
    refreshed: list[int] = []
    prov.on_tools_refreshed = refreshed.append
    assert await prov.reconnect() is True
    assert (client.close_calls, client.connect_calls) == (1, 1)
    assert [s.name for s in prov.list_tools()] == ["t1", "t2"] and refreshed == [2]  # 重拉清单后统一通知绑定处

    failing_client, failing = _provider()
    failing_client._connected = False
    failing_client.next_connect_result = False
    assert await failing.reconnect() is False  # 连不上：不拉清单

    empty_client, empty = _provider()
    empty_client._connected = False
    empty_client.next_connect_result = True
    assert await empty.reconnect() is False and empty.list_tools() == []  # 连上但无工具：视为重连未成


async def test_health_check_forwards_to_client_probe() -> None:
    class StubClient:
        def __init__(self, result: bool) -> None:
            self.result = result
            self.probe_calls = 0

        async def probe(self) -> bool:
            self.probe_calls += 1
            return self.result

    for result in (True, False):
        stub = StubClient(result)
        prov = McpToolProvider(client=stub, server_name="serverA")  # type: ignore[arg-type]
        assert await prov.health_check() is result and stub.probe_calls == 1


# 适配器声明：任务查询与资源订阅


class AdapterFakeClient(FakeMcpClient):
    """可记录订阅/退订、返回可配结构化结果的替身。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []
        self.callbacks: List[Any] = []
        self.structured: Any = {"state": "running", "task_id": "t1"}

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self.called.append({"name": tool_name, "arguments": arguments})
        return FakeResult(content=[], structured=self.structured)

    async def subscribe_resource(self, uri: str, callback: Any) -> Any:
        self.subscribed.append(uri)
        self.callbacks.append(callback)

        async def unsubscribe() -> None:
            self.unsubscribed.append(uri)

        return unsubscribe


async def _adapter_provider() -> tuple[AdapterFakeClient, McpToolProvider]:
    client = AdapterFakeClient(name="maicraft", config=AnyConfig())
    prov = McpToolProvider(client=client, server_name="maicraft", provider="maicraft")
    client.set_tools([FakeTool("task", "任务"), FakeTool("perceive", "观察")])
    await prov.setup()
    return client, prov


async def test_public_declarations_after_setup_drive_the_adapters() -> None:
    """绑定处只有在 setup 拉到工具清单后才认得全名，所以适配器字段必须支持"构造后按公开名赋值"。
    曾经字段私有、绑定处赋公开名，两边不同名 → 查询恒返回 None、订阅恒不建立且不报错，只有真实 provider 能拦住。"""
    client, prov = await _adapter_provider()
    prov.task_query_tool = "maicraft_task"
    prov.task_status_map = {"running": "running"}
    prov.attention_uri = "maicraft://attention"
    prov.attention_read_tool = "maicraft_perceive"
    prov.attention_read_arguments = {"view": "attention"}

    snapshot = await prov.query_task("t1")
    assert snapshot is not None and snapshot["status"] == "running"  # 必须真的调用 server 侧工具
    assert client.called[-1] == {"name": "task", "arguments": {"action": "get", "task_id": "t1"}}

    assert await prov.read_attention(stream_id="s-1", after_cursor=3, limit=5) == client.structured
    assert client.called[-1]["arguments"] == {
        "view": "attention",
        "after_cursor": 3,
        "limit": 5,
        "wait_ms": 0,
        "stream_id": "s-1",
    }

    assert prov.subscribe_task_notifications(lambda task_id: None) is not None
    await asyncio.sleep(0)
    assert client.subscribed == ["maicraft://attention"]


async def test_notifications_fan_out_and_only_last_unsubscribe_drops_the_resource() -> None:
    client, prov = await _adapter_provider()
    prov.attention_uri = "maicraft://attention"
    first: List[str] = []
    second: List[str] = []
    drop_first = prov.subscribe_task_notifications(first.append)
    drop_second = prov.subscribe_task_notifications(second.append)
    await asyncio.sleep(0)
    assert client.subscribed == ["maicraft://attention"]  # 多个订阅方共用一条资源订阅

    for callback in client.callbacks:
        callback("maicraft://attention")
    assert first == [""] and second == [""]

    assert drop_first is not None and drop_second is not None
    drop_first()
    for callback in client.callbacks:
        callback("maicraft://attention")
    assert first == [""] and second == ["", ""] and client.unsubscribed == []  # 还有订阅方时不断开

    drop_second()
    await asyncio.sleep(0)
    assert client.unsubscribed == ["maicraft://attention"]


async def test_read_attention_without_declaration_never_guesses_a_tool() -> None:
    client, prov = await _adapter_provider()
    assert await prov.read_attention() is None and client.called == []


# 业务拒绝不熔断，传输故障仍熔断


@pytest.mark.parametrize("as_exception", [True, False])
async def test_business_rejections_keep_tool_available_and_emit_errors(as_exception: bool) -> None:
    from fastmcp.exceptions import ToolError  # 可选依赖，只在需要模拟业务异常时加载

    class Endpoint:
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            if arguments.get("valid"):
                return SimpleNamespace(is_error=False, content=[], structured_content={"value": 1})
            if as_exception:
                raise ToolError('{"error":{"code":"invalid_arguments","message":"choose a valid field"}}')
            return SimpleNamespace(
                is_error=True,
                content=[{"type": "text", "text": "choose a valid field"}],
                structured_content={"error": {"code": "invalid_arguments"}},
            )

    client = McpClient(name="reference", config=SimpleNamespace())
    client._connected, client._client = True, Endpoint()
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="read", description="", provider="reference")]
    bus = EventBus(enable_stats=False)
    events: list[ToolResultPayload] = []
    delivered = asyncio.Event()

    async def observe(name: str, payload: ToolResultPayload, source: str) -> None:
        events.append(payload)
        if len(events) == 5:
            delivered.set()

    bus.on("tool.result.#", observe, ToolResultPayload)
    registry = ToolRegistry(event_bus=bus, failure_threshold=3)
    registry.register_provider(provider)
    try:
        for _ in range(4):  # 业务拒绝留给调用者纠正，次数超过熔断阈值也不能让健康工具从名单消失
            result = await registry.invoke(ToolInvocation(tool_name="reference_read"))
            assert not result.success and "choose a valid field" in result.error_message
            assert not registry.is_tripped("reference_read")
        assert client.connected and any(t.full_name == "reference_read" for t in registry.list_tools())
        assert (await registry.invoke(ToolInvocation(tool_name="reference_read", arguments={"valid": True}))).success
        await asyncio.wait_for(delivered.wait(), timeout=1)
        assert [e.status for e in events] == ["error"] * 4 + ["success"]  # 免熔断不能把请求失败伪装成成功
    finally:
        await bus.cleanup()


async def test_transport_failures_still_trip_the_tool() -> None:
    class Endpoint:
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            raise ConnectionError("connection reset")

    client = McpClient(name="reference", config=SimpleNamespace())
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="read", description="", provider="reference")]
    registry = ToolRegistry(failure_threshold=3)
    registry.register_provider(provider)
    for _ in range(3):
        client._connected, client._client = True, Endpoint()  # 每次恢复连接后仍传输故障，避免混入重连实现
        result = await registry.invoke(ToolInvocation(tool_name="reference_read"))
        assert not result.success and not client.connected
    assert registry.is_tripped("reference_read")
