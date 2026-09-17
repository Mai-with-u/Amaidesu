"""McpToolProvider 单测：缓存 specs + invoke 转发

mock McpClient（不发真实网络请求）——验证：
- setup() 预拉工具列表 → list_tools() 同步返回缓存（声明名 = server 原始名）
- invoke() 按派生全名对照 spec、原始名直呼 server、结果映射、未知工具失败、未连接降级
- provider 单名：name = provider（默认 server 名，可显式覆盖）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.models import ToolInvocation


class FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: Any = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class FakeContent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeResult:
    def __init__(self, content: Any = None, is_error: bool = False, structured: Any = None) -> None:
        self.content = content
        self.is_error = is_error
        self.structured_content = structured


class FakeMcpClient(McpClient):
    """测试替身：不发网络请求，记录调用。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connected = True
        self._tools: List[FakeTool] = []
        self.called: List[Dict[str, Any]] = []
        self.closed = False

    def set_tools(self, tools: List[FakeTool]) -> None:
        self._tools = tools

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        return self._connected

    async def list_tools(self) -> List[Any]:
        return list(self._tools) if self._connected else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self._connected:
            return None
        self.called.append({"name": tool_name, "arguments": arguments})
        return FakeResult(content=[FakeContent(f"ok:{tool_name}")], structured={"raw": tool_name})

    async def close(self) -> None:
        self.closed = True
        self._connected = False


def _provider(
    server_name: str = "serverA",
    provider: Optional[str] = None,
) -> tuple[FakeMcpClient, McpToolProvider]:
    client = FakeMcpClient(name=server_name, config=AnyConfig())
    prov = McpToolProvider(client=client, server_name=server_name, provider=provider)
    return client, prov


class AnyConfig:
    transport = "http"
    url = "http://127.0.0.1:8766/mcp"
    headers = {}
    env = {}
    args = []
    command = None
    enabled = True
    reconnect = True
    timeout_seconds = 30.0


class TestProvider:
    async def test_setup_caches_tools(self) -> None:
        client, prov = _provider()
        client.set_tools(
            [
                FakeTool("perceive", "观察"),
                FakeTool("execute", "执行", input_schema={"type": "object"}),
            ]
        )
        count = await prov.setup()
        assert count == 2
        specs = list(prov.list_tools())
        # 声明名 = server 原始名；对外全名派生 <provider>_<原始名>
        assert [s.name for s in specs] == ["perceive", "execute"]
        assert [s.full_name for s in specs] == ["serverA_perceive", "serverA_execute"]
        assert specs[0].provider == "serverA"
        assert specs[1].parameters_schema == {"type": "object"}

    async def test_setup_connection_failure_returns_zero(self) -> None:
        client, prov = _provider()
        client._connected = False
        count = await prov.setup()
        assert count == 0
        assert list(prov.list_tools()) == []

    async def test_invoke_calls_server_with_raw_name(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()

        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive", arguments={"view": "situation"}))
        assert result.success is True
        assert client.called == [{"name": "perceive", "arguments": {"view": "situation"}}]
        assert result.structured_content == {"raw": "perceive"}

    async def test_invoke_raw_name_already_prefixed_kept_as_is(self) -> None:
        # 回归：MaiCraft server 工具原名自带 maicraft_ 前缀。声明名原样保留
        # （不做任何前缀拼接/解析）；调用 server 时用原始名直呼
        client, prov = _provider()
        client.set_tools([FakeTool("maicraft_perceive", "观察")])
        await prov.setup()
        spec = list(prov.list_tools())[0]
        assert spec.name == "maicraft_perceive"
        assert spec.full_name == "serverA_maicraft_perceive"

        result = await prov.invoke(ToolInvocation(tool_name="serverA_maicraft_perceive"))
        assert result.success is True
        assert client.called == [{"name": "maicraft_perceive", "arguments": {}}]

    async def test_invoke_unknown_tool_returns_failure(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()

        result = await prov.invoke(ToolInvocation(tool_name="serverA_nonexistent"))
        assert result.success is False
        assert "不属于" in result.error_message
        assert client.called == []

    async def test_invoke_before_setup_returns_failure(self) -> None:
        client, prov = _provider()
        # 未 setup：specs 为空 → 任何工具都被视为不属于本 Provider
        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
        assert result.success is False

    async def test_invoke_call_failure_maps_to_error_result(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()
        client._connected = False  # 模拟断连 → call_tool 返回 None

        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
        assert result.success is False
        assert "失败" in result.error_message

    async def test_close_delegates_to_client(self) -> None:
        client, prov = _provider()
        await prov.close()
        assert client.closed is True

    async def test_provider_name_defaults_to_server_name(self) -> None:
        _, prov = _provider()
        assert prov.name == "serverA"

    async def test_provider_name_overridable(self) -> None:
        client, prov = _provider(provider="game")
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()
        # 覆盖 provider → 单名与全名前缀随之改变
        assert prov.name == "game"
        assert list(prov.list_tools())[0].full_name == "game_perceive"


class AdapterFakeClient(FakeMcpClient):
    """适配器测试替身：可记录订阅/退订，返回可配的结构化结果。"""

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


class TestAdapterDeclarations:
    """适配器声明的绑定契约。

    绑定处（Agent / 采集器）只能在 ``setup()`` 拉到工具清单之后才认得出工具全名，
    因此适配器字段必须支持"构造后按公开名赋值"。曾经字段是私有的、绑定处赋公开名，
    两边不同名 → 查询适配器恒返回 None、资源订阅恒不建立，且不报错；
    替身 provider 的测试完全看不见，只有这条真实 provider + 真实赋值的测试能拦住。
    """

    def _bind(self, provider: str = "maicraft") -> tuple[AdapterFakeClient, McpToolProvider]:
        client = AdapterFakeClient(name=provider, config=AnyConfig())
        prov = McpToolProvider(client=client, server_name=provider, provider=provider)
        return client, prov

    async def test_public_declarations_after_setup_drive_the_adapters(self) -> None:
        client, prov = self._bind()
        client.set_tools([FakeTool("task", "任务"), FakeTool("perceive", "观察")])
        await prov.setup()

        # 绑定处的写法：构造后按公开名声明
        prov.task_query_tool = "maicraft_task"
        prov.task_status_map = {"running": "running"}
        prov.attention_uri = "maicraft://attention"
        prov.attention_read_tool = "maicraft_perceive"
        prov.attention_read_arguments = {"view": "attention"}

        snapshot = await prov.query_task("t1")
        assert snapshot is not None, "查询适配器必须真的调用 server 侧工具，而不是恒返回 None"
        assert client.called[-1] == {"name": "task", "arguments": {"action": "get", "task_id": "t1"}}
        assert snapshot["status"] == "running"

        page = await prov.read_attention(stream_id="s-1", after_cursor=3, limit=5)
        assert page == client.structured
        arguments = client.called[-1]["arguments"]
        assert arguments == {
            "view": "attention",
            "after_cursor": 3,
            "limit": 5,
            "wait_ms": 0,
            "stream_id": "s-1",
        }

        handle = prov.subscribe_task_notifications(lambda task_id: None)
        assert handle is not None, "声明了 attention_uri 就必须建立资源订阅"
        await asyncio.sleep(0)
        assert client.subscribed == ["maicraft://attention"]

    async def test_notification_fans_out_and_last_unsubscribe_drops_it(self) -> None:
        client, prov = self._bind()
        client.set_tools([FakeTool("task", "任务")])
        await prov.setup()
        prov.attention_uri = "maicraft://attention"

        first: List[str] = []
        second: List[str] = []
        drop_first = prov.subscribe_task_notifications(lambda task_id: first.append(task_id))
        drop_second = prov.subscribe_task_notifications(lambda task_id: second.append(task_id))
        await asyncio.sleep(0)
        assert client.subscribed == ["maicraft://attention"], "多个订阅方共用一条资源订阅"

        # 上游通知 → 每个订阅方各自收到举旗
        for callback in client.callbacks:
            callback("maicraft://attention")
        assert first == [""] and second == [""]

        # 退掉一个：订阅仍在，另一个照常收到
        assert drop_first is not None
        drop_first()
        for callback in client.callbacks:
            callback("maicraft://attention")
        assert first == [""] and second == ["", ""]
        assert client.unsubscribed == [], "还有订阅方时不得断开资源订阅"

        # 退掉最后一个：才真正退订
        assert drop_second is not None
        drop_second()
        await asyncio.sleep(0)
        assert client.unsubscribed == ["maicraft://attention"]

    async def test_read_attention_returns_none_without_declaration(self) -> None:
        client, prov = self._bind()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()
        assert await prov.read_attention() is None
        assert client.called == [], "未声明读取工具时不得猜测工具名去调用"
