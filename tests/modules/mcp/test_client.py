"""McpClient：资源订阅与重订阅、连接/关闭期限、请求期限（静默不回包）、调用异常分诊、探活、连接失败日志去重

全部经由构造器注入接缝（``client_factory`` / ``transport_factory``）替换
传输与 SDK 客户端，不 patch 私有方法、不手撕内部状态。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.modules.mcp.client import McpClient, shared_ssl_context
from src.modules.mcp.config import McpServerConfig
from tests.modules.mcp.fakes import AnyConfig, Endpoint, FakeFastmcpClient, FakeTool, Updated, recording_client_factory
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools import ToolInvocation, ToolRegistry
from src.modules.tools.models import ToolExecutionResult


async def _connected_client() -> tuple:
    """经注入接缝建立连接的客户端；返回 (client, made)，made 收集会话替身实例。"""
    made: List[FakeFastmcpClient] = []
    client = McpClient(
        name="maicraft",
        config=AnyConfig(),
        transport_factory=lambda: object(),
        client_factory=recording_client_factory(made),
    )
    assert await client.connect() is True
    return client, made


def _injected_client(
    client_factory: Any,
    config: Any = None,
    transport_factory: Any = None,
    name: str = "reference",
) -> McpClient:
    """带接缝注入的客户端快捷构造；缺省给最简配置与哑传输。"""
    if config is None:
        config = SimpleNamespace(timeout_seconds=1, request_timeout_ms=1000, transport="http")
    if transport_factory is None:
        transport_factory = lambda: object()  # noqa: E731 - 哑传输：注入路径不使用传输对象本身
    return McpClient(name=name, config=config, transport_factory=transport_factory, client_factory=client_factory)


# 资源订阅


async def test_subscription_dispatches_by_uri_isolates_callback_errors_and_stops_after_unsubscribe() -> None:
    client, made = await _connected_client()
    fake = made[0]
    received: List[str] = []

    def boom(uri: str) -> None:
        raise ValueError("callback failed")

    unsubscribe = await client.subscribe_resource("maicraft://attention", received.append)
    await client.subscribe_resource("maicraft://a", boom)
    assert fake.session.subscribed == ["maicraft://attention", "maicraft://a"]

    handler = fake.message_handler
    await handler.on_resource_updated(Updated("maicraft://attention"))
    await handler.on_resource_updated(Updated("other://resource"))  # 未登记的 URI 静默忽略
    await handler.on_resource_updated(Updated("maicraft://a"))  # 回调抛错被兜底，不影响后续分发
    await handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"] * 2

    await unsubscribe()
    await handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"] * 2 and fake.session.unsubscribed == ["maicraft://attention"]


async def test_subscribe_needs_connection_and_disconnected_unsubscribe_only_removes_locally() -> None:
    made: List[FakeFastmcpClient] = []
    client = McpClient(
        name="maicraft",
        config=AnyConfig(),
        transport_factory=lambda: object(),
        client_factory=recording_client_factory(made),
    )
    with pytest.raises(RuntimeError):  # 未连接时显式失败，消费方可降级周期兜底
        await client.subscribe_resource("maicraft://attention", lambda uri: None)
    assert await client.read_resource("maicraft://attention") is None and await client.list_resources() == []

    await client.connect()
    made[0].list_results = ["res1"]
    assert await client.read_resource("maicraft://attention") == [("content",)]
    assert await client.list_resources() == ["res1"]

    unsubscribe = await client.subscribe_resource("maicraft://attention", lambda uri: None)
    await client.close()  # 断连后退订：本地除名，不发网络请求
    await unsubscribe()
    assert made[0].session.unsubscribed == []


async def test_reconnect_resubscribes_active_subscriptions_best_effort() -> None:
    # 会话替身的订阅行为由标志位在调用时刻决定：同一工厂贯穿全程，无需中途替换
    fail_on_subscribe = {"on": False}
    made: List[FakeFastmcpClient] = []

    def factory(transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> FakeFastmcpClient:
        fake = FakeFastmcpClient(transport, message_handler=message_handler, init_timeout=init_timeout)
        fake.session.fail_on_subscribe = fail_on_subscribe["on"]
        made.append(fake)
        return fake

    client = McpClient(
        name="maicraft",
        config=AnyConfig(),
        transport_factory=lambda: object(),
        client_factory=factory,
    )
    assert await client.connect() is True
    received: List[str] = []
    await client.subscribe_resource("maicraft://attention", received.append)
    first_session = made[0].session

    # 传输故障后重连：经公开 call_tool 路径触发断连标记（替身 call_tool 恒抛传输错误）
    assert await client.call_tool("any", {}) is None and client.connected is False
    assert await client.connect() is True
    assert made[1].session is not first_session
    assert made[1].session.subscribed == ["maicraft://attention"]  # 重发订阅
    await made[1].message_handler.on_resource_updated(Updated("maicraft://attention"))
    assert received == ["maicraft://attention"]  # 新连接的通知分发照常

    fail_on_subscribe["on"] = True
    assert await client.call_tool("any", {}) is None and client.connected is False
    assert await client.connect() is True  # 重订阅失败不阻断连接

    fail_on_subscribe["on"] = False
    assert await client.call_tool("any", {}) is None and client.connected is False
    assert await client.connect() is True
    assert "maicraft://attention" in made[-1].session.subscribed  # 失败的登记保留，下次重连再试


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


def _deadline_client(factory: Any) -> McpClient:
    return McpClient(
        "reference",
        SimpleNamespace(timeout_seconds=0.025, request_timeout_ms=100, transport="http"),
        transport_factory=lambda: object(),
        client_factory=factory,
    )


async def test_http_transports_reuse_one_ssl_context() -> None:
    # 连不上的 server 会反复重连：每次都新建 SSL 上下文要在事件循环里重新加载证书
    created: List[Any] = []
    for name in ("qq", "maicraft"):
        config = McpServerConfig(transport="http", url=f"http://127.0.0.1:8767/{name}")
        await McpClient(name=name, config=config, client_factory=recording_client_factory(created)).connect()
    context = await shared_ssl_context()
    assert [fake.transport.verify for fake in created] == [context, context]


async def test_handshake_and_close_honor_connection_timeout() -> None:
    # wait_for 是防挂死的兜底上限：内部期限只有 25ms，上限按满载并行估算留足余量，
    # 只断言"不拖住调用方"，不断言墙钟数字。
    stuck = _deadline_client(lambda *args, **kwargs: Session(stuck_enter=True))
    assert await asyncio.wait_for(stuck.connect(), timeout=10) is False  # 握手不返回时不能拖住首次工具调用
    assert not stuck.connected
    await stuck.close()

    old = Session(stuck_exit=True)
    client = _deadline_client(lambda *args, **kwargs: old)
    assert await client.connect() is True
    await asyncio.wait_for(client.close(), timeout=10)  # 旧会话退出卡住时也要释放本地引用
    assert not client.connected and old.exits == 1


async def test_concurrent_connects_share_one_session() -> None:
    made: list[Session] = []

    def create(*args: Any, **kwargs: Any) -> Session:
        made.append(Session())
        return made[-1]

    client = _deadline_client(create)
    try:
        assert await asyncio.gather(client.connect(), client.connect()) == [True, True]  # 避免相互关闭刚建立的连接
        assert len(made) == 1 and made[0].exits == 0
    finally:
        await client.close()


async def test_real_sdk_recovers_after_a_timed_out_call() -> None:
    from fastmcp import FastMCP  # 可选 SDK，服务在进程内运行，不连接游戏或调用模型

    service = FastMCP("deadline-test")
    calls: list[bool] = []

    @service.tool()
    async def inspect(wait: bool) -> str:
        calls.append(wait)
        if wait:
            await asyncio.Event().wait()
        return "available"

    client = McpClient(
        "reference",
        SimpleNamespace(timeout_seconds=1, request_timeout_ms=1000, transport="http"),
        transport_factory=lambda: service,  # fastmcp 直接接受进程内 server 对象作为传输
    )
    try:
        assert await client.connect()
        with pytest.raises(TimeoutError, match="远端结果未知"):
            await asyncio.wait_for(client.call_tool("inspect", {"wait": True}), timeout=4)
        result = await asyncio.wait_for(client.call_tool("inspect", {"wait": False}), timeout=4)
        assert result.content[0].text == "available" and client.connected
        # 恢复调用已确认送达（其结果被完整拿到），证明超时没有毒化会话；
        # 被取消的首次调用是否曾送达服务端取决于取消时机，不做时序断言
        assert False in calls
    finally:
        await client.close()


# 请求期限


class SilentEndpoint(Endpoint):
    """记录请求已收到后永不回包，复现通道静默而进程仍活着的情况。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.cancelled = False

    async def list_tools(self) -> List[Any]:
        return [FakeTool("operate")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def test_silent_tool_returns_unknown_outcome_without_replay(loguru_capture: Any) -> None:
    endpoint = SilentEndpoint()
    client = _injected_client(
        lambda *args, **kwargs: endpoint,
        config=SimpleNamespace(request_timeout_ms=25, timeout_seconds=1, transport="http"),
    )
    assert await client.connect() is True
    provider = McpToolProvider(client=client, server_name="reference")
    assert await provider.setup() == 1  # 经公开 setup 拉到工具清单，不再手工对齐内部 specs
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
    await client.close()
    assert endpoint.exits == 1  # 下次重连仍需释放旧会话，不能丢掉清理入口


async def test_caller_cancellation_remains_cancellation() -> None:
    endpoint = SilentEndpoint()
    client = _injected_client(
        lambda *args, **kwargs: endpoint,
        config=SimpleNamespace(request_timeout_ms=1000, timeout_seconds=1, transport="http"),
    )
    assert await client.connect() is True
    call = asyncio.create_task(client.call_tool("read", {}))
    await asyncio.sleep(0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):  # 用户主动中断不能变成可重试超时
        await call
    assert endpoint.calls == 1 and endpoint.cancelled
    await client.close()


@pytest.mark.parametrize(
    ("method", "arguments", "expected"),
    [("list_tools", (), []), ("list_resources", (), []), ("read_resource", ("resource://sample",), None)],
)
async def test_discovery_and_resource_reads_are_bounded(method: str, arguments: tuple, expected: Any) -> None:
    async def silent(*args: Any) -> Any:
        await asyncio.Event().wait()

    endpoint = Endpoint()
    setattr(endpoint, method, silent)  # 会话替身上挂载被测方法（公开名，非生产私有成员）
    client = _injected_client(
        lambda *args, **kwargs: endpoint,
        config=SimpleNamespace(request_timeout_ms=25, timeout_seconds=1, transport="http"),
    )
    assert await client.connect() is True
    assert await asyncio.wait_for(getattr(client, method)(*arguments), timeout=1) == expected and not client.connected
    await client.close()


# 调用异常分诊


class RaiserEndpoint(Endpoint):
    """call_tool 恒抛指定异常的会话替身；暴露一个工具供 Provider 公开拉取 specs。"""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    async def list_tools(self) -> List[Any]:
        return [FakeTool("speak")]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        raise self._exc


async def _failing_client(exc: Exception) -> tuple:
    """返回 (client, endpoint)：endpoint 供断言释放路径（exits 计数）。"""
    endpoint = RaiserEndpoint(exc)
    client = _injected_client(lambda *args, **kwargs: endpoint, config=SimpleNamespace(transport="http"), name="triage")
    assert await client.connect() is True
    return client, endpoint


async def test_business_error_keeps_connection_and_transport_error_marks_disconnected() -> None:
    from fastmcp.exceptions import ToolError

    business, _ = await _failing_client(ToolError('{"code":"invalid_arguments","message":"need label"}'))
    with pytest.raises(ToolError):  # server 正常应答的业务错误：round-trip 完整，连接无恙，异常上抛由 Provider 转述
        await business.call_tool("t", {})
    assert business.connected is True and await business.probe() is True  # 连接与会话实例都健在

    transport, transport_endpoint = await _failing_client(ConnectionError("connection reset"))
    assert await transport.call_tool("t", {}) is None  # 传输故障：标记断连，维持惰性重连语义
    assert transport.connected is False
    await transport.close()
    assert transport_endpoint.exits == 1  # 断开实例仍由关闭路径释放会话，清理入口不丢

    endpoint = RaiserEndpoint(ToolError('{"error":{"code":"invalid_arguments","message":"need label"}}'))
    client = _injected_client(
        lambda *args, **kwargs: endpoint, config=SimpleNamespace(transport="http"), name="maicraft"
    )
    assert await client.connect() is True
    provider = McpToolProvider(client=client, server_name="maicraft")
    assert await provider.setup() == 1  # 经公开 setup 拉到工具清单
    result = await provider.invoke(ToolInvocation(tool_name="maicraft_speak", arguments={}))
    assert isinstance(result, ToolExecutionResult) and result.success is False  # 永不外抛，错误文本透传
    assert "MCP 业务错误" in result.error_message and "invalid_arguments" in result.error_message
    assert await provider.health_check() is True  # 业务错误后连接仍健康
    await client.close()


# 探活与连接失败日志


class ProbeClient(McpClient):
    """connect 结果可编程的探活测试替身；连接态经 prime 按用例枚举预置。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.connect_calls = 0
        self.next_connect_result: Optional[bool] = None

    def prime(self, *, connected: bool, has_client: bool) -> None:
        """预置探活分支所需的连接态（替身自身状态，等价于真实连接后的取值域）。"""
        self._connected = connected
        self._client = object() if has_client else None

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        self.connect_calls += 1
        assert self.next_connect_result is not None
        return self.next_connect_result


@pytest.mark.parametrize(
    ("connected", "has_client", "connect_result", "probe", "connect_calls"),
    [
        (True, True, False, True, 0),  # 本地连接判据通过：不重连（若走了重连，结果会被 False 污染）
        (True, False, True, True, 1),  # 会话实例偶发丢失时走重连，防漏报
        (False, True, True, True, 1),
        (False, True, False, False, 1),
    ],
    ids=["cheap-path", "client-missing", "reconnect-ok", "reconnect-failed"],
)
async def test_probe_uses_local_state_before_reconnecting(connected, has_client, connect_result, probe, connect_calls):
    client = ProbeClient(name="serverA", config=AnyConfig())
    client.prime(connected=connected, has_client=has_client)
    client.next_connect_result = connect_result
    assert await client.probe() is probe and client.connect_calls == connect_calls


def _raise_with(message: str):
    def _raise() -> Any:
        raise RuntimeError(message)

    return _raise


async def test_same_connect_failure_warns_once_until_reason_changes_or_a_success_resets(loguru_capture) -> None:
    """常驻调用方周期性重连，"服务没开"是常态：同因只 warning 一次，理由变了或连上过再失败才重新报。"""
    state: Dict[str, Optional[str]] = {"message": "boom"}

    def transport_factory() -> Any:
        message = state["message"]
        if message is None:
            return object()
        raise RuntimeError(message)

    client = McpClient(
        name="probe",
        config=McpServerConfig(enabled=True, transport="http", url="http://127.0.0.1:9/mcp"),
        transport_factory=transport_factory,
        client_factory=lambda *args, **kwargs: Endpoint(),
    )

    def failures() -> int:
        return len([r for r in loguru_capture.records if r["level"] == "WARNING" and "连接失败" in r["message"]])

    assert await client.connect() is False and await client.connect() is False
    assert failures() == 1
    assert any(
        r["level"] == "DEBUG" and "同因" in r["message"] for r in loguru_capture.records
    )  # 重复失败降 debug 留痕

    state["message"] = "another"
    assert await client.connect() is False and failures() == 2

    state["message"] = None
    assert await client.connect() is True  # 连上一次，失败特征串复位

    state["message"] = "boom"
    await client.close()  # 已连接的服务再次断开才进入重连分支
    assert await client.connect() is False and failures() == 3


async def test_connect_failure_dedup_survives_instance_recreation(loguru_capture) -> None:
    """事件流采集器每轮重试都新建客户端实例：去重状态按 server 名共享，换实例不算"首次失败"。"""

    def make_client(message: str) -> McpClient:
        return McpClient(
            name="recreated",
            config=McpServerConfig(enabled=True, transport="http", url="http://127.0.0.1:9/mcp"),
            transport_factory=_raise_with(message),
        )

    def failures() -> int:
        return len([r for r in loguru_capture.records if r["level"] == "WARNING" and "连接失败" in r["message"]])

    assert await make_client("boom").connect() is False
    assert await make_client("boom").connect() is False and failures() == 1  # 新实例 + 同因：不再 warning
    assert any(r["level"] == "DEBUG" and "同因" in r["message"] for r in loguru_capture.records)

    assert await make_client("changed").connect() is False and failures() == 2  # 原因变了，重新 warning
