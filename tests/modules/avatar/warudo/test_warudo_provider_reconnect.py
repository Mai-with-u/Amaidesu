"""WarudoProvider.connect / disconnect / supports_reconnect 单测。

mock websockets（不发真实网络请求）——验证：
- connect 委托内部 _connect，返回 _is_connected 真值
- disconnect 仅关当前 websocket，**不动** _should_stop / 后台 _connection_loop / _has_started
- supports_reconnect 因覆写 connect → True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from src.modules.avatar.warudo.warudo_provider import WarudoProvider, create_warudo_provider


def _build_provider() -> WarudoProvider:
    provider = create_warudo_provider(config={"ws_host": "localhost", "ws_port": 19190})
    # 替换为受控 stub——避免 _connect 真实触发 websockets.connect
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    return provider


# =============================================================================
# supports_reconnect
# =============================================================================


def test_warudo_supports_reconnect() -> None:
    """WarudoProvider 覆写 connect → supports_reconnect=True。"""
    provider = _build_provider()
    assert provider.supports_reconnect is True


# =============================================================================
# connect
# =============================================================================


async def test_warudo_connect_returns_is_connected_when_connected() -> None:
    """connect：_is_connected=True → 返回 True。"""
    provider = _build_provider()
    provider._is_connected = True
    assert await provider.connect() is True


async def test_warudo_connect_returns_false_when_disconnected() -> None:
    """connect：_is_connected=False → 返回 False。"""
    provider = _build_provider()
    provider._is_connected = False
    assert await provider.connect() is False


# =============================================================================
# disconnect
# =============================================================================


async def test_warudo_disconnect_closes_websocket_and_keeps_loop() -> None:
    """disconnect：关闭 websocket + 置 _is_connected=False + 清空 _action_sender 引用，
    **不动** _should_stop 与后台 _connection_loop（保持 setup 语义）。"""
    provider = _build_provider()
    ws = MagicMock()
    ws.close = AsyncMock()
    provider.websocket = ws
    provider._is_connected = True
    provider._should_stop = False
    provider._has_started = True
    loop_task = MagicMock()
    provider._connection_task = loop_task

    assert await provider.disconnect() is True
    ws.close.assert_awaited_once()
    assert provider.websocket is None
    assert provider._is_connected is False
    # _should_stop 与 _connection_task 保持不变（后台重连循环继续驱动）
    assert provider._should_stop is False
    assert provider._connection_task is loop_task
    assert provider._has_started is True


async def test_warudo_disconnect_handles_missing_websocket() -> None:
    """disconnect：无 websocket 时也安全返回 True。"""
    provider = _build_provider()
    provider.websocket = None
    assert await provider.disconnect() is True


async def test_warudo_disconnect_swallows_close_exception() -> None:
    """disconnect：websocket.close 抛异常时也置 _is_connected=False，返回 True。"""
    provider = _build_provider()
    ws = MagicMock()
    ws.close = AsyncMock(side_effect=RuntimeError("boom"))
    provider.websocket = ws
    provider._is_connected = True

    assert await provider.disconnect() is True
    assert provider.websocket is None
    assert provider._is_connected is False


# =============================================================================
# reconnect 默认组合
# =============================================================================


async def test_warudo_reconnect_default_compose() -> None:
    """reconnect 默认组合：先 disconnect 后 connect，透传 connect bool。"""
    provider = _build_provider()
    provider._is_connected = True
    ws = MagicMock()
    ws.close = AsyncMock()
    provider.websocket = ws

    # stub _connect 已由 _build_provider 设好；让它把 _is_connected 置 True
    async def _fake_connect() -> None:
        provider._is_connected = True

    provider._connect = _fake_connect  # type: ignore[method-assign]
    assert await provider.reconnect() is True
    ws.close.assert_awaited_once()
    assert provider._is_connected is True  # reconnect 路径最终为已连接
