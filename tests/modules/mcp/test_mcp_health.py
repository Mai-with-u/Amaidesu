"""MCP 健康探活单测

覆盖：
- ``McpClient.probe()``：本地连接判据（cheap path）vs 重连（reconnect 路径）
- ``McpToolProvider.health_check()``：纯转发到 ``client.probe()``

mock 策略：继承 ``McpClient`` 替换 ``connect`` 计数，避免触发 fastmcp 延迟 import。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider


class _AnyConfig:
    """最小配置占位（与 test_provider.py 中的 AnyConfig 同样的复用目的）。"""

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


class ProbeFakeClient(McpClient):
    """probe 测试替身：跟踪 ``connect`` 调用次数。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # 默认处于"已连接"态，_client 设为非 None 占位
        self._connected = True
        self._client = object()
        self.connect_calls: int = 0
        self.next_connect_result: bool = True

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        self.connect_calls += 1
        return self.next_connect_result


class _StubClient:
    """``health_check`` 委托测试用：只暴露 ``probe`` 协程。"""

    def __init__(self, result: bool) -> None:
        self._result = result
        self.probe_calls: int = 0

    async def probe(self) -> bool:
        self.probe_calls += 1
        return self._result


class TestClientProbe:
    async def test_cheap_path_when_connected_and_client_set(self) -> None:
        """已连接 + _client 非 None：立即返回 True，不调 connect。"""
        client = ProbeFakeClient(name="serverA", config=_AnyConfig())
        # 反向断言：若走了重连，next_connect_result=False 必然把结果污染成 False
        client.next_connect_result = False

        result = await client.probe()

        assert result is True
        assert client.connect_calls == 0

    async def test_cheap_path_requires_client_not_none(self) -> None:
        """_connected=True 但 _client=None 时走重连（防 _client 偶发 None 漏报）。"""
        client = ProbeFakeClient(name="serverA", config=_AnyConfig())
        client._client = None
        client.next_connect_result = True

        result = await client.probe()

        assert result is True
        assert client.connect_calls == 1

    async def test_reconnect_when_disconnected_succeeds(self) -> None:
        """未连接 + connect 返回 True → probe True，调一次 connect。"""
        client = ProbeFakeClient(name="serverA", config=_AnyConfig())
        client._connected = False
        client.next_connect_result = True

        result = await client.probe()

        assert result is True
        assert client.connect_calls == 1

    async def test_reconnect_when_disconnected_fails(self) -> None:
        """未连接 + connect 返回 False → probe False，调一次 connect。"""
        client = ProbeFakeClient(name="serverA", config=_AnyConfig())
        client._connected = False
        client.next_connect_result = False

        result = await client.probe()

        assert result is False
        assert client.connect_calls == 1


class TestProviderHealthCheck:
    async def test_health_check_delegates_to_client_probe_true(self) -> None:
        stub = _StubClient(result=True)
        prov = McpToolProvider(client=stub, server_name="serverA")  # type: ignore[arg-type]

        result = await prov.health_check()

        assert result is True
        assert stub.probe_calls == 1

    async def test_health_check_delegates_to_client_probe_false(self) -> None:
        stub = _StubClient(result=False)
        prov = McpToolProvider(client=stub, server_name="serverA")  # type: ignore[arg-type]

        result = await prov.health_check()

        assert result is False
        assert stub.probe_calls == 1
