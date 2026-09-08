"""VTSProvider.connect / disconnect / supports_reconnect 单测。

mock pyvts（不发真实网络请求）——验证：
- connect 委托内部 _connect，返回 _is_connected 真值
- disconnect 委托内部 _disconnect，**不重置** _has_started
- supports_reconnect 因覆写 connect → True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from src.modules.avatar.vts.vts_provider import VTSProvider, create_vts_provider


def _build_provider() -> VTSProvider:
    provider = create_vts_provider(config={"vts_host": "localhost", "vts_port": 8001})
    matcher = MagicMock()
    matcher.hotkey_list = []
    matcher.find_by_name = MagicMock(return_value=None)
    matcher.trigger_hotkey = AsyncMock(return_value=True)
    matcher.load_hotkeys = AsyncMock()
    provider.hotkey_matcher = matcher
    return provider


# =============================================================================
# supports_reconnect
# =============================================================================


def test_vts_supports_reconnect() -> None:
    """VTSProvider 覆写 connect → supports_reconnect=True。"""
    provider = _build_provider()
    assert provider.supports_reconnect is True


# =============================================================================
# connect / disconnect
# =============================================================================


async def test_vts_connect_returns_is_connected_when_connected() -> None:
    """connect：内部 _connect 已建立 → 返回 _is_connected 真值。"""
    provider = _build_provider()
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    provider._is_connected = True
    assert await provider.connect() is True


async def test_vts_connect_returns_false_when_internal_fails() -> None:
    """connect：内部 _connect 失败（_is_connected 保持 False） → 返回 False。"""
    provider = _build_provider()
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    provider._is_connected = False
    assert await provider.connect() is False


async def test_vts_disconnect_calls_internal_disconnect() -> None:
    """disconnect 委托内部 _disconnect，返回 True 表达动作完成。"""
    provider = _build_provider()
    provider._disconnect = AsyncMock()  # type: ignore[method-assign]
    provider._has_started = True
    assert await provider.disconnect() is True
    provider._disconnect.assert_awaited_once_with()
    # _has_started 保持 True（不破坏 setup 语义）
    assert provider._has_started is True


# =============================================================================
# reconnect 默认组合（不覆写 → disconnect + connect）
# =============================================================================


async def test_vts_reconnect_default_compose() -> None:
    """reconnect 默认组合：先 disconnect 后 connect。"""
    provider = _build_provider()
    provider._disconnect = AsyncMock()  # type: ignore[method-assign]
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    provider._is_connected = True
    assert await provider.reconnect() is True
    provider._disconnect.assert_awaited_once_with()
    provider._connect.assert_awaited_once_with()
