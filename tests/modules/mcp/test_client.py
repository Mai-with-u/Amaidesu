"""McpClient：资源订阅与重订阅、连接/关闭期限、请求期限（静默不回包）、调用异常分诊、探活、连接失败日志去重"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any, List

import pytest

from src.modules.mcp.client import McpClient
from src.modules.mcp.config import McpServerConfig
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools import ToolInvocation, ToolRegistry, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from tests.modules.mcp.fakes import AnyConfig


class FakeSession:
    """记录订阅/退订请求的 session 替身。"""

    def __init__(self) -> None:
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []
        self.fail_on_subscribe = False

    async def subscribe_resource(self, uri: Any) -> None:
        if self.fail_on_subscribe:
            raise ConnectionError("subscribe refused")
        self.subscribed.append(str(uri))

    async def unsubscribe_resource(self, uri: Any) -> None:
        self.unsubscribed.append(str(uri))


class FakeFastmcpClient:
    """fastmcp.Client 替身：接住 message_handler、暴露 session。"""

    def __init__(self, transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> None:
        self.message_handler = message_handler
        self.session = FakeSession()
        self.read_results: List[Any] = [("content",)]
        self.list_results: List[Any] = []

    async def __aenter__(self) -> "FakeFastmcpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def read_resource(self, uri: str) -> Any:
        return self.read_results

    async def list_resources(self) -> List[Any]:
        return self.list_results


class Updated:
    """ResourceUpdatedNotification 形状替身：handler 只用 params.uri。"""

    def __init__(self, uri: str) -> None:
        self.params = SimpleNamespace(uri=uri)


@pytest.fixture
def fastmcp_patched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fastmcp.Client", FakeFastmcpClient)  # 生产代码 connect 内延迟 import 的目标


async def _connected_client() -> McpClient:
    client = McpClient(name="maicraft", config=AnyConfig())
    assert await client.connect() is True
    return client


# 资源订阅


@pytest.mark.usefixtures("fastmcp_patched")
async def test_subscription_dispatches_by_uri_isolates_callback_errors_and_stops_after_unsubscribe() -> None:
    client = await _connected_client()
    received: List[str] = []

    def boom(uri: str) -> None:
        raise ValueError("callback failed")

    unsubscribe = await client.subscribe_resource("maicraft://attention", received.append)
    await client.subscribe_resource("maicraft://a", boom)
    assert client._client.session.subscribed == ["maicraft://attention", "maicraft://a"]

    handler = client._client.message_handler
    await handler.on_resource_updated(Updated("maicraft://attention"))
    await handler.on_resource_updated(Updated("other://resource"))  # 未登记的 URI 静默忽略
    await handler.on_resource_updated(Updated("maicraft://a"))  # 回调抛错被兜底，不影响后续分发
    await handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"] * 2

    await unsubscribe()
    await handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"] * 2 and client._client.session.unsubscribed == ["maicraft://attention"]


@pytest.mark.usefixtures("fastmcp_patched")
async def test_subscribe_needs_connection_and_disconnected_unsubscribe_only_removes_locally() -> None:
    client = McpClient(name="maicraft", config=AnyConfig())
    with pytest.raises(RuntimeError):  # 未连接时显式失败，消费方可降级周期兜底
        await client.subscribe_resource("maicraft://attention", lambda uri: None)
    assert await client.read_resource("maicraft://attention") is None and await client.list_resources() == []

    await client.connect()
    client._client.list_results = ["res1"]
    assert await client.read_resource("maicraft://attention") == [("content",)]
    assert await client.list_resources() == ["res1"]

    unsubscribe = await client.subscribe_resource("maicraft://attention", lambda uri: None)
    client._connected = False
    await unsubscribe()  # 断连后退订：本地除名，不发网络请求
    assert client._subscriptions == {} and client._client.session.unsubscribed == []


@pytest.mark.usefixtures("fastmcp_patched")
async def test_reconnect_resubscribes_active_subscriptions_best_effort() -> None:
    client = await _connected_client()
    received: List[str] = []
    await client.subscribe_resource("maicraft://attention", received.append)
    first_session = client._client.session

    client._connected, client._client = False, None  # 传输故障后重连
    assert await client.connect() is True
    assert client._client.session is not first_session
    assert client._client.session.subscribed == ["maicraft://attention"]  # 重发订阅
    await client._client.message_handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"]  # 新连接的通知分发照常

    client._connected, client._client = False, None
    original = FakeFastmcpClient.__init__

    def failing_init(self, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.session.fail_on_subscribe = True

    FakeFastmcpClient.__init__ = failing_init  # type: ignore[method-assign]
    try:
        assert await client.connect() is True  # 重订阅失败不阻断连接
    finally:
        FakeFastmcpClient.__init__ = original  # type: ignore[method-assign]
    assert "maicraft://attention" in client._subscriptions  # 登记保留，下次重连再试


# 连接与关闭期限


class Session:
    """只替代 SDK 会话的建立和退出，保留生产通道的生命周期控制。"""

    def __init__(self, *, stuck_enter: bool = False, stuck_exit: bool = False) -> None:
        self.stuck_enter, self.stuck_exit, self.exits = stuck_enter, stuck_exit, 0

    async def __aenter__(self) -> "Session":
        if self.stuck_enter:
            await asyncio.Event().wait()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.exits += 1
        if self.stuck_exit:
            await asyncio.Event().wait()


def _deadline_client(monkeypatch: pytest.MonkeyPatch, factory: Any) -> McpClient:
    monkeypatch.setattr("fastmcp.Client", factory)
    client = McpClient("reference", SimpleNamespace(timeout_seconds=0.025, request_timeout_ms=100, transport="http"))
    monkeypatch.setattr(client, "_build_transport", lambda: object())
    monkeypatch.setattr(client, "_build_message_handler", lambda subscriptions: None)
    return client


async def test_handshake_and_close_honor_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = _deadline_client(monkeypatch, lambda *args, **kwargs: Session(stuck_enter=True))
    assert await asyncio.wait_for(stuck.connect(), timeout=1) is False  # 握手不返回时不能拖住首次工具调用
    assert not stuck.connected
    await stuck.close()

    client = _deadline_client(monkeypatch, lambda *args, **kwargs: Session())
    old = Session(stuck_exit=True)
    client._client, client._connected = old, True
    await asyncio.wait_for(client.close(), timeout=1)  # 旧会话退出卡住时也要释放本地引用
    assert not client.connected and client._client is None and old.exits == 1


async def test_concurrent_connects_share_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Session] = []

    def create(*args: Any, **kwargs: Any) -> Session:
        made.append(Session())
        return made[-1]

    client = _deadline_client(monkeypatch, create)
    try:
        assert await asyncio.gather(client.connect(), client.connect()) == [True, True]  # 避免相互关闭刚建立的连接
        assert len(made) == 1 and made[0].exits == 0
    finally:
        await client.close()


async def test_real_sdk_recovers_after_a_timed_out_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import FastMCP  # 可选 SDK，服务在进程内运行，不连接游戏或调用模型

    service = FastMCP("deadline-test")
    calls: list[bool] = []

    @service.tool()
    async def inspect(wait: bool) -> str:
        calls.append(wait)
        if wait:
            await asyncio.Event().wait()
        return "available"

    client = McpClient("reference", SimpleNamespace(timeout_seconds=1, request_timeout_ms=300, transport="http"))
    monkeypatch.setattr(client, "_build_transport", lambda: service)
    try:
        assert await client.connect()
        with pytest.raises(TimeoutError, match="远端结果未知"):
            await asyncio.wait_for(client.call_tool("inspect", {"wait": True}), timeout=2)
        result = await asyncio.wait_for(client.call_tool("inspect", {"wait": False}), timeout=2)
        assert result.content[0].text == "available" and calls == [True, False] and client.connected
    finally:
        await client.close()


# 请求期限


class SilentEndpoint:
    """记录请求已收到后永不回包，复现通道静默而进程仍活着的情况。"""

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def test_silent_tool_returns_unknown_outcome_without_replay(loguru_capture: Any) -> None:
    endpoint = SilentEndpoint()
    client = McpClient("reference", SimpleNamespace(request_timeout_ms=25, timeout_seconds=1))
    client._client, client._connected = endpoint, True
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="operate", description="", provider="reference")]
    registry = ToolRegistry()
    registry.register_provider(provider)

    result = await asyncio.wait_for(registry.invoke(ToolInvocation(tool_name="reference_operate")), timeout=1)
    assert not result.success and result.failure_kind == "execution"
    error = result.structured_content["error"]
    # 远端可能已经执行：本地超时只结束等待，不能声称未执行，也不自动再发一次
    assert error["code"] == "mcp_request_timeout" and error["outcome_known"] is False
    assert error["timeout_ms"] == 25 and error["request_id"]
    messages = [e["message"] for e in loguru_capture.records if error["request_id"] in e["message"]]
    assert any("开始" in m for m in messages) and any("结束 status=timeout" in m for m in messages)
    assert endpoint.calls == 1 and endpoint.cancelled and not client.connected
    assert client._client is endpoint  # 下次重连仍需释放旧会话，不能丢掉清理入口


async def test_caller_cancellation_remains_cancellation() -> None:
    endpoint = SilentEndpoint()
    client = McpClient("reference", SimpleNamespace(request_timeout_ms=1000, timeout_seconds=1))
    client._client, client._connected = endpoint, True
    call = asyncio.create_task(client.call_tool("read", {}))
    await asyncio.sleep(0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):  # 用户主动中断不能变成可重试超时
        await call
    assert endpoint.calls == 1 and endpoint.cancelled


@pytest.mark.parametrize(
    ("method", "arguments", "expected"),
    [("list_tools", (), []), ("list_resources", (), []), ("read_resource", ("resource://sample",), None)],
)
async def test_discovery_and_resource_reads_are_bounded(method: str, arguments: tuple, expected: Any) -> None:
    async def silent(*args: Any) -> Any:
        await asyncio.Event().wait()

    client = McpClient("reference", SimpleNamespace(request_timeout_ms=25, timeout_seconds=1))
    client._client, client._connected = SimpleNamespace(**{method: silent}), True
    assert await asyncio.wait_for(getattr(client, method)(*arguments), timeout=1) == expected and not client.connected


# 调用异常分诊


class _Raiser:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        raise self._exc


def _failing_client(exc: Exception) -> McpClient:
    client = McpClient(name="triage", config=SimpleNamespace())
    client._connected, client._client = True, _Raiser(exc)
    return client


async def test_business_error_keeps_connection_and_transport_error_marks_disconnected() -> None:
    from fastmcp.exceptions import ToolError

    business = _failing_client(ToolError('{"code":"invalid_arguments","message":"need label"}'))
    with pytest.raises(ToolError):  # server 正常应答的业务错误：round-trip 完整，连接无恙，异常上抛由 Provider 转述
        await business.call_tool("t", {})
    assert business._connected is True and business._client is not None

    transport = _failing_client(ConnectionError("connection reset"))
    assert await transport.call_tool("t", {}) is None  # 传输故障：标记断连，维持惰性重连语义
    assert transport._connected is False and transport._client is not None  # 断开实例仍要由重连/关闭路径释放

    provider = McpToolProvider(
        client=_failing_client(ToolError('{"error":{"code":"invalid_arguments","message":"need label"}}')),
        server_name="maicraft",
    )
    provider._specs = [ToolSpec(name="speak", description="", provider="maicraft")]
    result = await provider.invoke(ToolInvocation(tool_name="maicraft_speak", arguments={}))
    assert isinstance(result, ToolExecutionResult) and result.success is False  # 永不外抛，错误文本透传
    assert "MCP 业务错误" in result.error_message and "invalid_arguments" in result.error_message
    assert provider._client._connected is True


# 探活与连接失败日志


class ProbeClient(McpClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connected, self._client = True, object()  # 默认已连接，_client 设为非 None 占位
        self.connect_calls = 0
        self.next_connect_result = True

    async def connect(self, timeout_seconds: float | None = None) -> bool:
        self.connect_calls += 1
        return self.next_connect_result


@pytest.mark.parametrize(
    ("connected", "has_client", "connect_result", "probe", "connect_calls"),
    [
        (True, True, False, True, 0),  # 本地连接判据通过：不重连（若走了重连，结果会被 False 污染）
        (True, False, True, True, 1),  # _client 偶发 None 时走重连，防漏报
        (False, True, True, True, 1),
        (False, True, False, False, 1),
    ],
    ids=["cheap-path", "client-missing", "reconnect-ok", "reconnect-failed"],
)
async def test_probe_uses_local_state_before_reconnecting(connected, has_client, connect_result, probe, connect_calls):
    client = ProbeClient(name="serverA", config=AnyConfig())
    client._connected = connected
    client._client = object() if has_client else None
    client.next_connect_result = connect_result
    assert await client.probe() is probe and client.connect_calls == connect_calls


class _OkFastmcp:
    def __init__(self, transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> None:
        self.session = None

    async def __aenter__(self) -> "_OkFastmcp":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _raise_with(message: str):
    def _raise() -> Any:
        raise RuntimeError(message)

    return _raise


@pytest.fixture
def _reset_shared_connect_failures() -> Any:
    """连接失败特征串按 server 名跨实例共享：测前测后清空，防同名 server 的测试间串扰。"""
    from src.modules.mcp import client as mcp_client_module

    mcp_client_module._last_connect_failures.clear()
    yield
    mcp_client_module._last_connect_failures.clear()


async def test_same_connect_failure_warns_once_until_reason_changes_or_a_success_resets(
    monkeypatch, loguru_capture, _reset_shared_connect_failures
) -> None:
    """常驻调用方周期性重连，"服务没开"是常态：同因只 warning 一次，理由变了或连上过再失败才重新报。"""
    client = McpClient(
        name="probe", config=McpServerConfig(enabled=True, transport="http", url="http://127.0.0.1:9/mcp")
    )

    def failures() -> int:
        return len([r for r in loguru_capture.records if r["level"] == "WARNING" and "连接失败" in r["message"]])

    monkeypatch.setattr(client, "_build_transport", _raise_with("boom"))
    assert await client.connect() is False and await client.connect() is False
    assert failures() == 1
    assert any(
        r["level"] == "DEBUG" and "同因" in r["message"] for r in loguru_capture.records
    )  # 重复失败降 debug 留痕

    monkeypatch.setattr(client, "_build_transport", _raise_with("another"))
    assert await client.connect() is False and failures() == 2

    monkeypatch.setattr(McpClient, "_build_message_handler", staticmethod(lambda subscriptions: None))
    monkeypatch.setitem(sys.modules, "fastmcp", types.SimpleNamespace(Client=_OkFastmcp))
    monkeypatch.setattr(client, "_build_transport", lambda: object())
    assert await client.connect() is True  # 连上一次，失败特征串复位

    monkeypatch.setattr(client, "_build_transport", _raise_with("boom"))
    client._connected = False  # 已连接的服务再次断开才进入重连分支
    assert await client.connect() is False and failures() == 3


async def test_connect_failure_dedup_survives_instance_recreation(
    monkeypatch, loguru_capture, _reset_shared_connect_failures
) -> None:
    """事件流采集器每轮重试都新建客户端实例：去重状态按 server 名共享，换实例不算"首次失败"。"""

    def make_client() -> McpClient:
        return McpClient(
            name="recreated", config=McpServerConfig(enabled=True, transport="http", url="http://127.0.0.1:9/mcp")
        )

    def failures() -> int:
        return len([r for r in loguru_capture.records if r["level"] == "WARNING" and "连接失败" in r["message"]])

    # 挂在类上（覆盖换出来的每个新实例）时是绑定方法，替身要接住 self
    def raise_boom(self: Any) -> Any:
        raise RuntimeError("boom")

    def raise_changed(self: Any) -> Any:
        raise RuntimeError("changed")

    monkeypatch.setattr(McpClient, "_build_transport", raise_boom)
    assert await make_client().connect() is False
    assert await make_client().connect() is False and failures() == 1  # 新实例 + 同因：不再 warning
    assert any(r["level"] == "DEBUG" and "同因" in r["message"] for r in loguru_capture.records)

    monkeypatch.setattr(McpClient, "_build_transport", raise_changed)
    assert await make_client().connect() is False and failures() == 2  # 原因变了，重新 warning
