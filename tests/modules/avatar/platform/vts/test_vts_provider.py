"""VTSProvider：热键触发、预设动作目录、情绪表情、待机参数解析、换模跟随、连接/断开/重连与注册表降级换血（不连真实 VTS）"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from src.modules.avatar.platform.vts.vts_provider import VTSProvider, create_vts_provider, register_vts_tools
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.types.emotion_vocab import Emotion

VTS_TOOLS = {"vts_set_expression", "vts_list_preset_actions", "vts_trigger_preset_action", "vts_set_idle_enabled"}


def _build_provider(
    config: Optional[Dict[str, Any]] = None, hotkey_list: Optional[List[Dict[str, Any]]] = None
) -> VTSProvider:
    provider = create_vts_provider(config={"vts_host": "localhost", "vts_port": 8001, **(config or {})})
    matcher = MagicMock()  # 热键匹配器换成受控替身，不触网
    matcher.hotkey_list = hotkey_list or []
    matcher.find_by_name = MagicMock(return_value=None)
    matcher.trigger_hotkey = AsyncMock(return_value=True)
    matcher.load_hotkeys = AsyncMock()
    provider.hotkey_matcher = matcher
    return provider


async def _invoke(provider: VTSProvider, tool: str, **arguments):
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments, source="test"))


async def test_trigger_hotkey_prefers_name_then_falls_back_to_id() -> None:
    provider = _build_provider()
    matcher = provider.hotkey_matcher

    matcher.find_by_name.return_value = "uuid-1"
    assert await provider.trigger_hotkey(name="Wave", hotkey_id="ignored") is True
    matcher.trigger_hotkey.assert_awaited_once_with("uuid-1")  # 按名解析命中时不用 id

    matcher.reset_mock()
    matcher.find_by_name.return_value = None
    assert await provider.trigger_hotkey(name="不存在", hotkey_id="uuid-2") is True
    matcher.trigger_hotkey.assert_awaited_once_with("uuid-2")  # 按名未命中，id 兜底

    matcher.reset_mock()
    assert await provider.trigger_hotkey(hotkey_id="uuid-3") is True
    matcher.find_by_name.assert_not_called()

    matcher.reset_mock()
    assert await provider.trigger_hotkey(name="不存在") is False  # 名未命中且无 id
    assert await provider.trigger_hotkey() is False
    matcher.trigger_hotkey.assert_not_awaited()


async def test_preset_action_catalog_comes_from_result_not_description() -> None:
    assert (await _build_provider(hotkey_list=[]).list_preset_actions()).structured_content == {"actions": []}

    provider = _build_provider(
        hotkey_list=[{"name": "Wave", "hotkeyID": "u1", "type": "ToggleAction"}, {"name": "Nod", "hotkeyID": "u2"}]
    )
    result = await provider.list_preset_actions()
    assert [a["name"] for a in result.structured_content["actions"]] == ["Wave", "Nod"]

    provider.mark_connected()  # 工具表仅连接态非空
    specs = provider.list_tools()
    assert {s.full_name for s in specs} == VTS_TOOLS and all(s.provider == "vts" for s in specs)
    expression = next(s for s in specs if s.full_name == "vts_set_expression")
    assert "Wave" not in expression.description  # 描述不承载目录，目录现取

    provider.mark_disconnected()
    assert provider.list_tools() == []  # 未连接（含装配期）工具表为空：降级登记，Agent 不可见不可调


async def test_invoke_trigger_preset_action_known_unknown_and_missing_args() -> None:
    provider = _build_provider(hotkey_list=[{"name": "Wave", "hotkeyID": "u1"}])
    provider.hotkey_matcher.find_by_name.return_value = "uuid-9"
    assert (await _invoke(provider, "vts_trigger_preset_action", action="Wave")).success is True
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-9")

    provider.hotkey_matcher.find_by_name.return_value = None
    unknown = await _invoke(provider, "vts_trigger_preset_action", action="不存在")
    assert unknown.success is False and "Wave" in unknown.structured_content["available_actions"]
    assert (await _invoke(provider, "vts_trigger_preset_action")).success is False


async def test_set_expression_scales_by_intensity_and_rejects_unknown_emotion() -> None:
    provider = _build_provider()
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)

    assert (await _invoke(provider, "vts_set_expression", emotion="happy", intensity=0.5)).success is True
    assert provider.expression.set_multi_parameter.await_args.args[0] == {"MouthSmile": 0.4, "Brows": 0.3}
    assert (await _invoke(provider, "vts_set_expression", emotion="这不是情绪")).success is False
    assert set(provider._emotion_map) == {e.value for e in Emotion}  # 映射表覆盖词表全部 17 值


async def test_idle_binding_resolution_is_config_only() -> None:
    tracking = ["FaceAngleX", "FaceAngleY", "FaceAngleZ", "MouthOpen"]

    provider = _build_provider()
    provider.expression.list_tracking_parameters = AsyncMock(return_value=tracking)
    resolved, unavailable = await provider._resolve_idle_parameter_names()
    assert resolved["head_x"] == "FaceAngleX" and unavailable == []

    disabled = _build_provider(config={"idle_param_body_x": "", "idle_param_head_x": ""})
    disabled.expression.list_tracking_parameters = AsyncMock(return_value=tracking)
    resolved, unavailable = await disabled._resolve_idle_parameter_names()
    assert (resolved["head_x"], resolved["body_x"], unavailable) == ("", "", [])  # 空名 = 该轴停用，不告警

    missing = _build_provider(config={"idle_param_body_x": "TorsoX"})
    missing.expression.list_tracking_parameters = AsyncMock(return_value=tracking)
    _, unavailable = await missing._resolve_idle_parameter_names()
    assert unavailable == ["TorsoX"]  # 不在 VTS 参数清单：进不可用清单，由调用方预置停写


async def test_follow_model_switch_reruns_reload_chain_only_on_real_change() -> None:
    for queried, reloads in (("ModelB", 1), ("ModelA", 0), ("", 0)):  # 换模、同模、查询不到
        provider = _build_provider()
        provider._current_model_name = "ModelA"
        provider._query_current_model_name = AsyncMock(return_value=queried)
        provider._reload_model_state = AsyncMock()
        await provider._follow_model_switch()
        assert provider._reload_model_state.await_count == reloads
        assert provider._current_model_name == (queried or "ModelA")


# 连接 / 断开 / 重连


async def test_connect_disconnect_reconnect_delegate_to_internal_actions() -> None:
    provider = _build_provider()
    assert provider.supports_reconnect is True
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    provider._disconnect = AsyncMock()  # type: ignore[method-assign]

    provider._is_connected = True
    assert await provider.connect() is True
    provider._is_connected = False
    assert await provider.connect() is False  # 内部连接失败时 _is_connected 保持 False

    provider._has_started = True
    assert await provider.disconnect() is True and provider._has_started is True  # 断开不破坏 setup 语义

    provider._disconnect.reset_mock()
    provider._connect.reset_mock()
    provider._is_connected = True
    assert await provider.reconnect() is True  # 默认组合：先断开后连接
    provider._disconnect.assert_awaited_once_with()
    provider._connect.assert_awaited_once_with()


async def test_connect_restores_automatic_reconnect_loop_exactly_once() -> None:
    provider = _build_provider()
    provider._connect = AsyncMock()  # type: ignore[method-assign]
    provider._RECONNECT_INTERVAL_S = 999  # 拉回的循环停在首个 sleep，测试尾取消

    provider._has_started = False
    await provider.connect()
    assert provider._reconnect_task is None  # setup 未执行前不拉循环，装配语义还没建立

    provider._has_started = True
    assert await provider.connect() is False  # 手动断开后再连，无论成败都要把自动重连拉回，VTS 之后恢复才能自愈
    task = provider._reconnect_task
    assert task is not None and not task.done()

    await provider.connect()
    assert provider._reconnect_task is task  # 循环仍在运行时不重复拉起
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _stub_successful_connect(provider: VTSProvider) -> None:
    """替身 pyvts 与解析链，让 _connect 走完整成功路径而不触网。"""
    provider._vts = MagicMock(
        connect=AsyncMock(), request_authenticate_token=AsyncMock(), request_authenticate=AsyncMock()
    )
    provider._reload_model_state = AsyncMock()  # type: ignore[method-assign]
    provider._query_current_model_name = AsyncMock(return_value="hiyori")  # type: ignore[method-assign]
    provider.idle_motion = MagicMock()


async def test_connection_transitions_fire_callback_and_maintain_last_error() -> None:
    ok = _build_provider()
    ok.last_error = "VTS 连接失败: 旧错"
    _stub_successful_connect(ok)
    fired: list[bool] = []
    ok.on_connection_changed = fired.append
    await ok._connect()
    assert (ok._is_connected, ok.last_error, fired) == (True, "", [True])  # 成功：清 last_error 并回调

    failed = _build_provider()
    failed._vts = MagicMock(connect=AsyncMock(side_effect=OSError("远程计算机拒绝网络连接")))
    fired = []
    failed.on_connection_changed = fired.append
    await failed._connect()
    assert failed._is_connected is False and fired == []  # 失败：只记 last_error（工具页降级徽标数据源）
    assert "VTS 连接失败" in failed.last_error and "远程计算机拒绝网络连接" in failed.last_error


async def test_dead_probe_and_manual_disconnect_fire_false_callback_once() -> None:
    provider = _build_provider()
    provider._is_connected = True
    provider._vts = MagicMock(close=AsyncMock())
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
    assert fired == [False] and provider._is_connected is False  # 探活判死：连接态翻转并回调，断连工具由此摘除

    other = _build_provider()
    other._is_connected = True
    other._vts = MagicMock(close=AsyncMock())
    other.idle_motion = MagicMock(stop=AsyncMock())
    fired = []
    other.on_connection_changed = fired.append
    await other._disconnect()
    await other._disconnect()  # 已断开再断开不重复触发
    assert fired == [False]


async def test_health_check_delegates_to_real_probe() -> None:
    provider = _build_provider()
    for result in (True, False):
        provider._vts_health_check = AsyncMock(return_value=result)  # type: ignore[method-assign]
        assert await provider.health_check() is result


def test_register_vts_tools_registers_degraded_then_follows_connection_state() -> None:
    registry = ToolRegistry()
    provider = register_vts_tools(registry, config={"vts_host": "localhost", "vts_port": 8001})
    card = next(p for p in registry.list_providers() if p["name"] == "vts")
    assert card["tool_count"] == 0 and registry.list_tools(provider="vts") == []  # 装配期降级登记 0 工具

    provider.mark_connected()
    assert {s.full_name for s in registry.list_tools(provider="vts")} == VTS_TOOLS  # 连上后补注册

    provider.mark_disconnected()
    assert registry.list_tools(provider="vts") == []  # 断连摘除
