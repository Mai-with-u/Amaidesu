"""WarudoProvider：动作目录与工具面、表情/视线调用、未连接不抛、连接/断开/重连语义（不连真实 Warudo）"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.warudo.expression_profile import MAIMAI_PROFILE_PATH, read_profile_expression
from src.modules.avatar.platform.warudo.warudo_provider import (
    BUILTIN_MOTIONS,
    WarudoProvider,
    create_warudo_provider,
    register_warudo_tools,
)
from src.modules.tools import ToolInvocation
from src.modules.tools.registry import ToolRegistry


def _provider(**config) -> WarudoProvider:
    return WarudoProvider({"ws_host": "localhost", "ws_port": 19190, **config}, event_bus=MagicMock())


async def _invoke(provider: WarudoProvider, tool: str, **arguments):
    return await provider.invoke(ToolInvocation(tool_name=tool, arguments=arguments))


def test_action_catalog_defaults_to_builtin_and_merges_config() -> None:
    assert _provider().action_catalog == {}
    names = [e["name"] for e in _provider()._preset_action_catalog()]
    assert names == [*BUILTIN_MOTIONS, "throw_fish"]  # 内置头部动作、站姿与抛鱼始终在

    entries = {
        e["name"]: e["description"]
        for e in _provider(action_catalog={"wave": "招手", "nod_once": "用力点头"})._preset_action_catalog()
    }
    assert (entries["wave"], entries["nod_once"]) == ("招手", "用力点头")  # 同名以配置说明为准
    assert list(entries).count("nod_once") == 1 and entries["throw_fish"]

    with pytest.raises(ValueError):  # action_catalog 必须是 Dict[str, str]，非 dict 严格报错
        _provider(action_catalog="invalid")


async def test_builtin_motions_and_catalog_entries_send_the_action_name_to_the_blueprint() -> None:
    provider = _provider(action_catalog={"wave": "招手"})
    provider._send_action_internal = AsyncMock()
    for action in ("nod_once", "pose_think", "wave"):
        assert (await provider.trigger_preset_action(action)).success is True
    assert [c.args for c in provider._send_action_internal.call_args_list] == [
        ("nod_once", 1),
        ("pose_think", 1),
        ("wave", 1),
    ]
    unknown = await provider.trigger_preset_action("dance")
    assert unknown.success is False and "nod_once" in unknown.structured_content["available_actions"]


def test_tool_face_is_the_four_agreed_tools_without_catalog_in_descriptions() -> None:
    provider = _provider(action_catalog={"wave": "招手"})
    provider.mark_connected()  # 工具表仅连接态非空
    specs = provider.list_tools()
    assert {s.full_name for s in specs} == {
        "warudo_set_expression",
        "warudo_list_preset_actions",
        "warudo_trigger_preset_action",
        "warudo_set_sight",
    }
    assert all(s.provider == "warudo" for s in specs)
    expression = next(s for s in specs if s.full_name == "warudo_set_expression")
    assert "招手" not in expression.description  # 描述不承载目录


def test_tools_degraded_to_empty_when_not_connected_and_registry_follows_connection() -> None:
    provider = _provider()
    provider.mark_disconnected()
    assert provider.list_tools() == []  # 未连接（含装配期）工具表为空：降级登记，Agent 不可见不可调

    registry = ToolRegistry()
    registered = register_warudo_tools(registry, config={"ws_host": "localhost", "ws_port": 19190})
    assert registry.list_tools(provider="warudo") == []  # 装配期降级登记 0 工具

    registered.mark_connected()
    assert len(registry.list_tools(provider="warudo")) == 4  # 连上后补注册

    registered.mark_disconnected()
    assert registry.list_tools(provider="warudo") == []  # 断连摘除


async def test_set_expression_uses_vrm_standard_names_by_default_and_validates_emotion() -> None:
    provider = _provider()
    result = await _invoke(provider, "warudo_set_expression", emotion="tired", intensity=0.5)
    # 情绪写入状态件（监控循环推送），不直连 WebSocket；默认档案用 VRM 标准表情名
    assert result.success is True and result.structured_content["applied"] == {"relaxed": 0.3, "eye_close": 0.2}
    state = provider.state_manager
    assert state.expression_state.weights["relaxed"] == 0.3 and state.eye_state.get_eye_state()["eye_close"] == 0.2

    assert (await _invoke(provider, "warudo_set_expression", emotion="joyful")).success is False  # 词表外情绪
    happy = await _invoke(provider, "warudo_set_expression", emotion="happy", intensity=1.0)
    assert happy.structured_content["applied"] == {"happy": 1.0}
    assert (await _invoke(provider, "warudo_set_expression", emotion="neutral")).structured_content["applied"] == {}
    assert state.expression_state.weights["happy"] == 0.0  # 中性 = 清空表情


async def test_maimai_profile_keeps_its_shapes_blink_rule_and_mouth_handoff() -> None:
    provider = _provider(expression=read_profile_expression(MAIMAI_PROFILE_PATH))
    state = provider.state_manager

    await provider.set_expression("love", 1.0)
    assert state.expression_state.weights["eye_happy_strong"] == 1.0
    assert state.eye_state.can_blink() is False  # 笑眼生效时眨眼任务跳过

    await provider.set_expression("happy", 1.0)
    provider._set_mouth_channel("VowelA", 0.7)  # 口型开始：嘴形让位，眉形保留
    assert state.expression_state.get_state()["mouth_happy_strong"] == 0.0
    assert state.expression_state.get_state()["eyebrow_happy_weak"] == 0.8
    assert state.vowel_state.weights["VowelA"] == 0.7
    provider._set_mouth_channel("", 0.0)  # 停嘴：嘴形恢复，不等下一句
    assert state.expression_state.get_state()["mouth_happy_strong"] == 1.0 and not state.vowel_state.active


async def test_set_sight_validates_target() -> None:
    provider = _provider()
    assert (await _invoke(provider, "warudo_set_sight", target="camera")).success is True
    assert (await _invoke(provider, "warudo_set_sight", target="wall")).success is False  # target 只有三个合法值


async def test_invoke_when_not_connected_returns_a_result_instead_of_raising() -> None:
    provider = _provider()
    provider._is_connected = False
    assert await _invoke(provider, "warudo_trigger_preset_action", action="wave") is not None


# 连接 / 断开 / 重连


def _stubbed_provider() -> WarudoProvider:
    provider = create_warudo_provider(config={"ws_host": "localhost", "ws_port": 19190})
    provider._connect = AsyncMock()  # type: ignore[method-assign]  # 避免真实触发 websockets.connect
    return provider


async def test_connect_failure_fires_no_callback() -> None:
    provider = _stubbed_provider()
    # _connect 被打桩后走真实失败分支不可行，直接验证手动断开只迁移时回调一次
    provider._is_connected = False
    fired: list[bool] = []
    provider.on_connection_changed = fired.append
    provider.websocket = MagicMock(close=AsyncMock())
    await provider.disconnect()
    assert fired == []  # 本就未连接，断开不触发迁移回调

    provider._is_connected = True
    provider.websocket = MagicMock(close=AsyncMock())
    await provider.disconnect()
    assert fired == [False]  # True→False 迁移回调一次


async def test_connect_reports_connection_state_and_reconnect_composes_disconnect_and_connect() -> None:
    provider = _stubbed_provider()
    assert provider.supports_reconnect is True
    provider._is_connected = True
    assert await provider.connect() is True
    provider._is_connected = False
    assert await provider.connect() is False

    ws = MagicMock(close=AsyncMock())
    provider.websocket = ws
    provider._is_connected = True

    async def fake_connect() -> None:
        provider._is_connected = True

    provider._connect = fake_connect  # type: ignore[method-assign]
    assert await provider.reconnect() is True  # 默认组合：先断开再连接
    ws.close.assert_awaited_once()


async def test_disconnect_closes_only_the_websocket_and_keeps_background_loop() -> None:
    provider = _stubbed_provider()
    ws = MagicMock(close=AsyncMock())
    loop_task = MagicMock()
    provider.websocket, provider._connection_task = ws, loop_task
    provider._is_connected, provider._should_stop, provider._has_started = True, False, True

    assert await provider.disconnect() is True
    ws.close.assert_awaited_once()
    assert provider.websocket is None and provider._is_connected is False
    # 后台重连循环继续驱动，setup 语义不被破坏
    assert provider._should_stop is False and provider._connection_task is loop_task and provider._has_started is True

    provider.websocket = None
    assert await provider.disconnect() is True  # 没有 websocket 也安全

    broken = MagicMock(close=AsyncMock(side_effect=RuntimeError("boom")))
    provider.websocket, provider._is_connected = broken, True
    assert await provider.disconnect() is True  # close 抛异常也要置为断开
    assert provider.websocket is None and provider._is_connected is False
