"""VTSProvider 单元测试（无需 VTS 外部环境）

覆盖：
- trigger_hotkey 按名解析优先 / hotkey_id 兜底 / 双空失败（内部方法）
- list_preset_actions 目录走结果（描述不承载目录）
- invoke 分发 vts_trigger_preset_action 的 action → find_by_name → trigger 链
- 工具 spec 的 provider 标识
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.vts.vts_provider import VTSProvider, create_vts_provider
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


def _build_provider(config: Optional[Dict[str, Any]] = None, hotkey_list: Optional[List[Dict[str, Any]]] = None) -> VTSProvider:
    provider = create_vts_provider(config={"vts_host": "localhost", "vts_port": 8001, **(config or {})})
    # 替换热键匹配器为受控 stub（不触网）
    matcher = MagicMock()
    matcher.hotkey_list = hotkey_list or []
    matcher.find_by_name = MagicMock(return_value=None)
    matcher.trigger_hotkey = AsyncMock(return_value=True)
    matcher.load_hotkeys = AsyncMock()
    provider.hotkey_matcher = matcher
    return provider


# =============================================================================
# trigger_hotkey：按名优先 / id 兜底
# =============================================================================


@pytest.mark.asyncio
async def test_trigger_hotkey_by_name_resolves_id():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = "uuid-1"

    assert await provider.trigger_hotkey(name="Wave") is True

    provider.hotkey_matcher.find_by_name.assert_called_once_with("Wave")
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-1")


@pytest.mark.asyncio
async def test_trigger_hotkey_falls_back_to_id_when_name_unmatched():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = None

    assert await provider.trigger_hotkey(name="不存在", hotkey_id="uuid-2") is True

    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-2")


@pytest.mark.asyncio
async def test_trigger_hotkey_fails_when_name_unmatched_and_no_id():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = None

    assert await provider.trigger_hotkey(name="不存在") is False
    provider.hotkey_matcher.trigger_hotkey.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_hotkey_fails_when_both_empty():
    provider = _build_provider()

    assert await provider.trigger_hotkey() is False
    provider.hotkey_matcher.find_by_name.assert_not_called()
    provider.hotkey_matcher.trigger_hotkey.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_hotkey_by_id_only():
    provider = _build_provider()

    assert await provider.trigger_hotkey(hotkey_id="uuid-3") is True
    provider.hotkey_matcher.find_by_name.assert_not_called()
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-3")


# =============================================================================
# list_preset_actions / trigger_preset_action：目录走结果，不进描述
# =============================================================================


@pytest.mark.asyncio
async def test_list_preset_actions_empty_when_no_hotkeys():
    provider = _build_provider(hotkey_list=[])
    result = await provider.list_preset_actions()
    assert result.success is True
    assert result.structured_content == {"actions": []}


@pytest.mark.asyncio
async def test_list_preset_actions_lists_hotkey_names():
    provider = _build_provider(
        hotkey_list=[
            {"name": "Wave", "hotkeyID": "u1", "type": "ToggleAction"},
            {"name": "Nod", "hotkeyID": "u2", "type": "TriggerAction"},
        ]
    )
    result = await provider.list_preset_actions()
    names = [a["name"] for a in result.structured_content["actions"]]
    assert names == ["Wave", "Nod"]


def test_list_tools_description_does_not_carry_catalog():
    """描述不承载目录（目录走 list_preset_actions 现取，注册期快照缺陷不再必要）。"""
    provider = _build_provider(hotkey_list=[{"name": "Wave", "hotkeyID": "u1"}])

    names = {s.full_name for s in provider.list_tools()}
    assert names == {
        "vts_set_expression",
        "vts_list_preset_actions",
        "vts_trigger_preset_action",
        "vts_set_idle_enabled",
    }
    expression = next(s for s in provider.list_tools() if s.full_name == "vts_set_expression")
    assert "Wave" not in expression.description


def test_list_tools_provider_identifier():
    provider = _build_provider()
    for spec in provider.list_tools():
        assert spec.provider == "vts"


# =============================================================================
# invoke 分发：vts_trigger_preset_action 参数契约
# =============================================================================


@pytest.mark.asyncio
async def test_invoke_trigger_preset_action_with_known_name():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = "uuid-9"

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_trigger_preset_action", arguments={"action": "Wave"}, source="test")
    )

    assert result.success is True
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-9")


@pytest.mark.asyncio
async def test_invoke_trigger_preset_action_unknown_name_returns_catalog():
    provider = _build_provider(hotkey_list=[{"name": "Wave", "hotkeyID": "u1"}])
    provider.hotkey_matcher.find_by_name.return_value = None

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_trigger_preset_action", arguments={"action": "不存在"}, source="test")
    )

    assert result.success is False
    assert "Wave" in result.structured_content["available_actions"]


@pytest.mark.asyncio
async def test_invoke_trigger_preset_action_without_args_fails_gracefully():
    provider = _build_provider()

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_trigger_preset_action", arguments={}, source="test")
    )

    assert result.success is False


# =============================================================================
# set_expression：情绪映射 + 强度缩放
# =============================================================================


@pytest.mark.asyncio
async def test_set_expression_scales_params_by_intensity():
    provider = _build_provider()
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_set_expression", arguments={"emotion": "happy", "intensity": 0.5}, source="test")
    )

    assert result.success is True
    args = provider.expression.set_multi_parameter.await_args
    written = args.args[0]
    assert written == {"MouthSmile": 0.4, "BrowLeftY": 0.3, "BrowRightY": 0.3}


@pytest.mark.asyncio
async def test_set_expression_unknown_emotion_fails():
    provider = _build_provider()

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_set_expression", arguments={"emotion": "这不是情绪"}, source="test")
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_set_expression_covers_seventeen_emotions():
    """词表 17 值全部在映射表中（映射键与 Emotion 词表一致）。"""
    from src.modules.types.emotion_vocab import Emotion

    provider = _build_provider()
    assert set(provider._emotion_map.keys()) == {e.value for e in Emotion}


# =============================================================================
# idle 绑定解析：纯配置（无候选猜测）、空停用、缺失警告停写
# =============================================================================


@pytest.mark.asyncio
async def test_resolve_idle_bindings_config_only_and_unavailable_reported():
    """绑定名解析纯配置：配置名原样返回，注入面外的名字进不可用清单。"""
    provider = _build_provider()
    provider.expression.list_tracking_parameters = AsyncMock(
        return_value=["FaceAngleX", "FaceAngleY", "FaceAngleZ", "MouthOpen"]
    )

    resolved, unavailable = await provider._resolve_idle_parameter_names()

    assert resolved["head_x"] == "FaceAngleX"
    assert unavailable == []


@pytest.mark.asyncio
async def test_resolve_idle_bindings_empty_means_disabled():
    """空配置名 = 该轴停用（不进不可用清单、不产生警告）。"""
    provider = _build_provider(config={"idle_param_body_x": "", "idle_param_head_x": ""})
    provider.expression.list_tracking_parameters = AsyncMock(
        return_value=["FaceAngleX", "FaceAngleY", "FaceAngleZ"]
    )

    resolved, unavailable = await provider._resolve_idle_parameter_names()

    assert resolved["head_x"] == ""
    assert resolved["body_x"] == ""
    assert unavailable == []


@pytest.mark.asyncio
async def test_resolve_idle_bindings_unavailable_name_flagged():
    """非空名不在注入面 → 返回不可用清单（调用方预置 failed_params 停写）。"""
    provider = _build_provider(config={"idle_param_body_x": "TorsoX"})
    provider.expression.list_tracking_parameters = AsyncMock(
        return_value=["FaceAngleX", "FaceAngleY", "FaceAngleZ"]
    )

    _resolved, unavailable = await provider._resolve_idle_parameter_names()

    assert unavailable == ["TorsoX"]


def test_mark_failed_params_preset_stops_writes():
    """解析期预置的失败参数：idle 循环停写（复用既有失败集机制）。"""
    controller = _build_idle_controller()
    controller.mark_failed_params(["TorsoX", ""])
    assert "TorsoX" in controller._failed_params
    assert "" not in controller._failed_params


def _build_idle_controller():
    from src.modules.avatar.platform.vts.idle_motion_controller import IdleMotionController

    async def _fake_set_parameter(name: str, value: float) -> bool:
        return True

    return IdleMotionController(
        logger_name="test.idle.mark",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=_fake_set_parameter,
    )
