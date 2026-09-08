"""McpClient 资源订阅单测

覆盖：
- ``subscribe_resource``：经 session 发标准订阅请求 + 回调登记
- 通知分发：fastmcp MessageHandler 按 URI 分发回调（注册表外 URI 忽略）
- 退订句柄：本地除名 + 发退订请求；未连接时退订只除名不发请求
- 重连重订阅：connect 成功后对活跃订阅集合重发请求（尽力而为）
- ``read_resource`` / ``list_resources``：直通 fastmcp；未连接兜底

mock 策略：monkeypatch ``fastmcp.Client`` 为替身（session 属性 + message_handler
挂载点），与生产代码的延迟 import 路径对齐。
"""

from __future__ import annotations

from typing import Any, List

import pytest

from src.modules.mcp.client import McpClient


class _AnyConfig:
    """最小配置占位（与 test_mcp_health.py 的 AnyConfig 同目的）。"""

    transport = "http"
    url = "http://127.0.0.1:8766/mcp"
    headers = {}
    env = {}
    args = []
    command = None
    prefix = None
    enabled = True
    reconnect = True
    timeout_seconds = 30.0


class _FakeSession:
    """记录订阅/退订请求的 session 替身。"""

    def __init__(self) -> None:
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []
        # 指定为非 None 时，subscribe/unsubscribe 抛错（模拟失败路径）
        self.fail_on_subscribe: bool = False

    async def subscribe_resource(self, uri: Any) -> None:
        if self.fail_on_subscribe:
            raise ConnectionError("subscribe refused")
        self.subscribed.append(str(uri))

    async def unsubscribe_resource(self, uri: Any) -> None:
        self.unsubscribed.append(str(uri))


class _FakeFastmcpClient:
    """fastmcp.Client 替身：接住 message_handler、暴露 session。"""

    instances: List["_FakeFastmcpClient"] = []

    def __init__(self, transport: Any, message_handler: Any = None) -> None:
        self.transport = transport
        self.message_handler = message_handler
        self.session = _FakeSession()
        self.read_results: List[Any] = [("content",)]
        self.list_results: List[Any] = []
        self.exited = False
        _FakeFastmcpClient.instances.append(self)

    async def __aenter__(self) -> "_FakeFastmcpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True

    async def read_resource(self, uri: str) -> Any:
        return self.read_results

    async def list_resources(self) -> List[Any]:
        return self.list_results


class _FakeNotification:
    """ResourceUpdatedNotification 形状替身（仅 params.uri 被 handler 触及）。"""

    def __init__(self, uri: str) -> None:
        self.params = type("P", (), {"uri": uri})()


@pytest.fixture()
def fastmcp_patched(monkeypatch: pytest.MonkeyPatch) -> type:
    """替换 fastmcp.Client（生产代码 connect 内延迟 import 的目标）。"""
    _FakeFastmcpClient.instances = []
    monkeypatch.setattr("fastmcp.Client", _FakeFastmcpClient)
    return _FakeFastmcpClient


async def _connect(client: McpClient) -> None:
    assert await client.connect() is True


async def test_subscribe_sends_request_and_registers_callback(fastmcp_patched: type) -> None:
    """订阅：发标准订阅请求 + 登记回调；通知到达时按 URI 分发。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    received: List[str] = []
    unsubscribe = await client.subscribe_resource("maicraft://attention", received.append)

    assert client._client.session.subscribed == ["maicraft://attention"]

    handler = client._client.message_handler
    await handler.on_resource_updated(_FakeNotification("maicraft://attention"))
    assert received == ["maicraft://attention"]

    await unsubscribe()
    await handler.on_resource_updated(_FakeNotification("maicraft://attention"))
    assert received == ["maicraft://attention"]  # 退订后不再分发
    assert client._client.session.unsubscribed == ["maicraft://attention"]


async def test_notification_for_unknown_uri_is_ignored(fastmcp_patched: type) -> None:
    """未登记 URI 的更新通知静默忽略（回调不误触）。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    received: List[str] = []
    await client.subscribe_resource("maicraft://attention", received.append)

    await client._client.message_handler.on_resource_updated(_FakeNotification("other://resource"))
    assert received == []


async def test_callback_exception_does_not_break_dispatch(fastmcp_patched: type) -> None:
    """回调抛异常被兜底：不影响 handler 自身与后续分发。"""

    def boom(uri: str) -> None:
        raise ValueError("callback failed")

    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    received: List[str] = []
    await client.subscribe_resource("maicraft://a", boom)
    await client.subscribe_resource("maicraft://b", received.append)

    handler = client._client.message_handler
    await handler.on_resource_updated(_FakeNotification("maicraft://a"))  # 抛错被吞
    await handler.on_resource_updated(_FakeNotification("maicraft://b"))  # 后续分发正常
    assert received == ["maicraft://b"]


async def test_unsubscribe_while_disconnected_only_removes_locally(fastmcp_patched: type) -> None:
    """断连后退订：本地除名生效，不发网络退订请求（连接已无意义）。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    unsubscribe = await client.subscribe_resource("maicraft://attention", lambda uri: None)

    client._connected = False
    await unsubscribe()

    assert client._subscriptions == {}
    assert client._client.session.unsubscribed == []


async def test_subscribe_requires_connection(fastmcp_patched: type) -> None:
    """未连接时订阅显式失败（RuntimeError）——消费方可降级周期兜底。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    with pytest.raises(RuntimeError):
        await client.subscribe_resource("maicraft://attention", lambda uri: None)


async def test_reconnect_resubscribes_active_subscriptions(fastmcp_patched: type) -> None:
    """重连成功后对活跃订阅集合重发订阅请求；新连接的通知照常分发。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    received: List[str] = []
    await client.subscribe_resource("maicraft://attention", received.append)
    first_session = client._client.session

    # 断连（模拟传输故障）→ 重连
    client._connected = False
    client._client = None
    await _connect(client)

    second_session = client._client.session
    assert second_session is not first_session
    assert second_session.subscribed == ["maicraft://attention"]  # 重发订阅

    await client._client.message_handler.on_resource_updated(_FakeNotification("maicraft://attention"))
    assert received == ["maicraft://attention"]  # 新连接通知分发照常


async def test_reconnect_resubscribe_failure_keeps_entry(fastmcp_patched: type) -> None:
    """重发订阅失败：记日志保留登记（下次重连再试），不阻断连接成功。"""
    client = McpClient(name="maicraft", config=_AnyConfig())
    await _connect(client)
    await client.subscribe_resource("maicraft://attention", lambda uri: None)

    client._connected = False
    client._client = None
    await _connect(client)
    client._client.session.fail_on_subscribe = True

    client._connected = False
    client._client = None
    assert await client.connect() is True  # 重订阅失败不阻断连接
    assert "maicraft://attention" in client._subscriptions  # 登记保留


async def test_read_and_list_resources_passthrough(fastmcp_patched: type) -> None:
    """read_resource / list_resources 直通 fastmcp；未连接时兜底 None/空列表。"""
    client = McpClient(name="maicraft", config=_AnyConfig())

    assert await client.read_resource("maicraft://attention") is None
    assert await client.list_resources() == []

    await _connect(client)
    client._client.list_results = ["res1"]
    assert await client.read_resource("maicraft://attention") == [("content",)]
    assert await client.list_resources() == ["res1"]
