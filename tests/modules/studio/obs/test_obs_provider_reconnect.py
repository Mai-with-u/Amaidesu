"""OBSProvider.connect / disconnect / supports_reconnect 单测。

mock obsws-python（不发真实 WebSocket 请求）——验证：
- connect 委托内部 _connect_obs，透传 bool
- disconnect 仅关 obs_connection，**不动** _has_started（保持 setup 语义）
- supports_reconnect 因覆写 connect → True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.studio.obs import obs_provider as obs_mod


def _make_obs_provider(monkeypatch: pytest.MonkeyPatch) -> "obs_mod.OBSProvider":
    monkeypatch.setattr(obs_mod, "obs", MagicMock())
    return obs_mod.OBSProvider(
        config={
            "type": "obs_control",
            "host": "127.0.0.1",
            "port": 4455,
            "password": None,
            "text_source_name": "text",
            "typewriter_enabled": False,
            "typewriter_speed": 0.1,
            "typewriter_delay": 0.5,
            "test_on_connect": False,
        },
        event_bus=None,
    )


# =============================================================================
# supports_reconnect
# =============================================================================


def test_obs_supports_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSProvider 覆写 connect → supports_reconnect=True。"""
    provider = _make_obs_provider(monkeypatch)
    assert provider.supports_reconnect is True


# =============================================================================
# connect
# =============================================================================


async def test_obs_connect_delegates_to_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect 委托 _connect_obs，透传 bool。"""
    provider = _make_obs_provider(monkeypatch)
    provider._connect_obs = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await provider.connect() is True
    provider._connect_obs.assert_awaited_once_with()


async def test_obs_connect_returns_false_on_internal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """_connect_obs 返回 False → connect 同步返回 False。"""
    provider = _make_obs_provider(monkeypatch)
    provider._connect_obs = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await provider.connect() is False


async def test_obs_connect_does_not_reset_has_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect 不重置 _has_started（手动连接是 setup 内的刷新动作）。"""
    provider = _make_obs_provider(monkeypatch)
    provider._connect_obs = AsyncMock(return_value=True)  # type: ignore[method-assign]
    provider._has_started = True
    await provider.connect()
    assert provider._has_started is True


# =============================================================================
# disconnect
# =============================================================================


async def test_obs_disconnect_closes_connection_and_keeps_has_started(monkeypatch: pytest.MonkeyPatch) -> None:
    """disconnect 关闭 obs_connection + 置 is_connected=False，**不重置** _has_started。"""
    provider = _make_obs_provider(monkeypatch)
    conn = MagicMock()
    conn.disconnect = MagicMock()
    provider.obs_connection = conn
    provider.is_connected = True
    provider._has_started = True

    assert await provider.disconnect() is True
    conn.disconnect.assert_called_once()
    assert provider.obs_connection is None
    assert provider.is_connected is False
    assert provider._has_started is True  # 关键：不重置


async def test_obs_disconnect_handles_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """disconnect：obs_connection 为 None 时安全返回 True。"""
    provider = _make_obs_provider(monkeypatch)
    provider.obs_connection = None
    assert await provider.disconnect() is True


async def test_obs_disconnect_swallows_close_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """disconnect：obs_connection.disconnect 抛异常时也置本地句柄清空。"""
    provider = _make_obs_provider(monkeypatch)
    conn = MagicMock()
    conn.disconnect = MagicMock(side_effect=RuntimeError("boom"))
    provider.obs_connection = conn
    provider.is_connected = True

    assert await provider.disconnect() is True
    assert provider.obs_connection is None
    assert provider.is_connected is False


# =============================================================================
# reconnect 默认组合
# =============================================================================


async def test_obs_reconnect_default_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """reconnect 默认组合：先 disconnect 后 connect，透传 connect bool。"""
    provider = _make_obs_provider(monkeypatch)
    provider._connect_obs = AsyncMock(return_value=True)  # type: ignore[method-assign]
    conn = MagicMock()
    conn.disconnect = MagicMock()
    provider.obs_connection = conn
    provider.is_connected = True

    assert await provider.reconnect() is True
    conn.disconnect.assert_called_once()
    provider._connect_obs.assert_awaited_once_with()
