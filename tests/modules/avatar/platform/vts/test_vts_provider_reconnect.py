"""VTSProvider.connect / disconnect / supports_reconnect 单测。

mock pyvts（不发真实网络请求）——验证：
- connect 委托内部 _connect，返回 _is_connected 真值
- disconnect 委托内部 _disconnect，**不重置** _has_started
- supports_reconnect 因覆写 connect → True
- 连接态迁移触发 on_connection_changed 回调（注册表换血工具集的驱动源）
- last_error 随连接成败维护（工具页降级徽标数据源）
- health_check 真实探活委托
- register_vts_tools 接线：降级登记 0 工具 → 连接补注册 → 断连摘除
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock


from src.modules.avatar.platform.vts.vts_provider import (
    VTSProvider,
    create_vts_provider,
    register_vts_tools,
)


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


# =============================================================================
# 连接态迁移回调 / last_error / health_check
# =============================================================================


def _make_connect_success_stub(provider: VTSProvider) -> None:
    """替身 pyvts 实例 + 解析链桩：让 _connect 走完整成功路径（零网络）。"""
    vts_stub = MagicMock()
    vts_stub.connect = AsyncMock()
    vts_stub.request_authenticate_token = AsyncMock()
    vts_stub.request_authenticate = AsyncMock()
    provider._vts = vts_stub
    provider._reload_model_state = AsyncMock()  # type: ignore[method-assign]
    provider._query_current_model_name = AsyncMock(return_value="hiyori")  # type: ignore[method-assign]
    provider.idle_motion = MagicMock()


async def test_connect_success_fires_callback_and_clears_last_error() -> None:
    """连接成功：置位 → 清 last_error → 回调 fire(True)（装配期降级登记由此补注册）。"""
    provider = _build_provider()
    provider.last_error = "VTS 连接失败: 旧错"
    _make_connect_success_stub(provider)
    fired: list[bool] = []
    provider.on_connection_changed = fired.append

    await provider._connect()

    assert provider._is_connected is True
    assert provider.last_error == ""
    assert fired == [True]


async def test_connect_failure_sets_last_error_without_callback() -> None:
    """连接失败：记 last_error（工具页降级徽标数据源）、不发迁移回调。"""
    provider = _build_provider()
    vts_stub = MagicMock()
    vts_stub.connect = AsyncMock(side_effect=OSError("远程计算机拒绝网络连接"))
    provider._vts = vts_stub
    fired: list[bool] = []
    provider.on_connection_changed = fired.append

    await provider._connect()

    assert provider._is_connected is False
    assert "VTS 连接失败" in provider.last_error
    assert "远程计算机拒绝网络连接" in provider.last_error
    assert fired == []


async def test_reconnect_loop_dead_detection_fires_callback() -> None:
    """重连循环探活判死：连接态翻转 + 回调 fire(False)（断连工具由此摘除）。"""
    provider = _build_provider()
    provider._is_connected = True
    provider._vts = MagicMock()
    provider._vts.close = AsyncMock()
    provider._vts_health_check = AsyncMock(return_value=False)  # type: ignore[method-assign]
    provider._connect = AsyncMock()  # type: ignore[method-assign]  # 后续重连迭代不触网
    provider._RECONNECT_INTERVAL_S = 0.01
    fired: list[bool] = []
    provider.on_connection_changed = fired.append

    task = asyncio.create_task(provider._reconnect_loop())
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if fired:
                break
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert fired == [False]
    assert provider._is_connected is False


async def test_disconnect_fires_callback_only_on_true_transition() -> None:
    """手动断开 / cleanup：True→False 迁移触发回调；已断开再断开不重复触发。"""
    provider = _build_provider()
    provider._is_connected = True
    provider._vts = MagicMock()
    provider._vts.close = AsyncMock()
    provider.idle_motion = MagicMock()
    provider.idle_motion.stop = AsyncMock()
    fired: list[bool] = []
    provider.on_connection_changed = fired.append

    await provider._disconnect()
    assert fired == [False]

    await provider._disconnect()  # 已断开：早退，不重复触发
    assert fired == [False]


async def test_health_check_delegates_to_real_probe() -> None:
    """health_check 委托真实探活（不再沿用基类恒 True 的默认实现）。"""
    provider = _build_provider()
    provider._vts_health_check = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await provider.health_check() is True
    provider._vts_health_check = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await provider.health_check() is False


# =============================================================================
# register_vts_tools 接线：降级登记 ↔ 连接态换血
# =============================================================================


def test_register_vts_tools_degrades_and_replenishes() -> None:
    """装配期降级登记 0 工具（Provider 在册可重连）；连接态迁移驱动注册表换血。"""
    from src.modules.tools.registry import ToolRegistry

    registry = ToolRegistry()
    provider = register_vts_tools(registry, config={"vts_host": "localhost", "vts_port": 8001})

    card = next(p for p in registry.list_providers() if p["name"] == "vts")
    assert card["tool_count"] == 0
    assert registry.list_tools(provider="vts") == []

    provider._is_connected = True
    provider._fire_connection_changed(True)
    assert {s.full_name for s in registry.list_tools(provider="vts")} == {
        "vts_set_expression",
        "vts_list_preset_actions",
        "vts_trigger_preset_action",
        "vts_set_idle_enabled",
    }

    provider._is_connected = False
    provider._fire_connection_changed(False)
    assert registry.list_tools(provider="vts") == []
