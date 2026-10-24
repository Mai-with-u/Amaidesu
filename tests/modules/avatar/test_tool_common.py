"""avatar.tool_common：契约工具 schema/specs 公共件、dispatch 分发三路径、unknown_action_fail 载荷与强度钳制"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.modules.avatar.tool_common import (
    contract_tool_specs,
    clamp_intensity,
    dispatch_tool,
    scaled_emotion_weights,
    set_expression_schema,
    trigger_action_schema,
    unknown_action_fail,
)
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, tool_result_ok
from src.modules.types.emotion_vocab import Emotion


def _invoke(tool: str, **arguments):
    return ToolInvocation(tool_name=tool, arguments=arguments, source="test")


def test_set_expression_schema_matches_agreed_contract() -> None:
    schema = set_expression_schema()
    assert schema["required"] == ["emotion"]
    emotion = schema["properties"]["emotion"]
    assert emotion["enum"] == [e.value for e in Emotion]  # 17 枚举值与词表同源
    intensity = schema["properties"]["intensity"]
    assert (intensity["minimum"], intensity["maximum"], intensity["default"]) == (0.0, 1.0, 0.5)
    assert "1.0 为该情绪的完整幅度" in intensity["description"]  # 强度语义的契约表述


def test_trigger_action_schema_references_own_list_tool_full_name() -> None:
    schema = trigger_action_schema("vts_list_preset_actions")
    assert schema["required"] == ["action"]
    assert "vts_list_preset_actions" in schema["properties"]["action"]["description"]


def test_contract_tool_specs_take_keyword_descriptions_and_derive_provider() -> None:
    specs = contract_tool_specs(
        "warudo",
        set_expression="Warudo 情绪描述",
        list_preset_actions="Warudo 目录描述",
        trigger_preset_action="Warudo 触发描述",
    )
    assert [s.name for s in specs] == ["set_expression", "list_preset_actions", "trigger_preset_action"]  # 不带前缀
    assert [s.full_name for s in specs] == [
        "warudo_set_expression",
        "warudo_list_preset_actions",
        "warudo_trigger_preset_action",
    ]
    assert all(s.kind == "sync" and s.provider == "warudo" for s in specs)
    assert [s.description for s in specs] == ["Warudo 情绪描述", "Warudo 目录描述", "Warudo 触发描述"]
    # trigger 的参数 schema 引用自家 list 工具全名（前缀随 provider 派生）
    assert "warudo_list_preset_actions" in specs[2].parameters_schema["properties"]["action"]["description"]
    assert specs[0].parameters_schema == set_expression_schema()


async def test_dispatch_tool_hits_handler_and_covers_unknown_and_exception() -> None:
    ok_handler = AsyncMock(return_value=tool_result_ok("vts_set_expression", True))
    logger = MagicMock()
    handlers = {"vts_set_expression": ok_handler}

    result = await dispatch_tool(_invoke("vts_set_expression", emotion="happy"), "vts", handlers, logger)
    assert result.success is True
    ok_handler.assert_awaited_once()

    unknown = await dispatch_tool(_invoke("vts_nope"), "vts", handlers, logger)
    assert unknown.success is False
    assert unknown.error_message == "工具 'vts_nope' 不属于 Provider 'vts'"

    def _boom() -> ToolExecutionResult:
        raise RuntimeError("连接已断")

    failure = await dispatch_tool(_invoke("vts_boom"), "vts", {"vts_boom": _boom}, logger)
    assert failure.success is False and failure.error_message == "RuntimeError: 连接已断"
    logger.exception.assert_called_once()  # 异常路径携带原因上下文，不静默吞


def test_unknown_action_fail_carries_catalog() -> None:
    result = unknown_action_fail("vts_trigger_preset_action", "不存在", ["Wave", "Nod"])
    assert result.success is False
    assert result.error_message == "未知预设动作 '不存在'"
    assert result.structured_content == {"available_actions": ["Wave", "Nod"]}
    assert result.content == str(["Wave", "Nod"])


def test_clamp_intensity_bounds() -> None:
    assert clamp_intensity(0.5) == 0.5
    assert clamp_intensity(-1.0) == 0.0
    assert clamp_intensity(2.0) == 1.0
    assert clamp_intensity("0.7") == 0.7  # 字符串数值经 float 归一


def test_scaled_emotion_weights_validates_scales_and_defaults_neutral() -> None:
    """VTS / Warudo 共用的缩放语义：词表外拒绝、按强度线性缩放、档案缺档按中性空表。"""
    table = {"happy": {"MouthSmile": 0.8}, "tired": {"relaxed": 0.6, "eye_close": 0.4}}

    factor, scaled = scaled_emotion_weights("happy", 0.5, table)
    assert (factor, scaled) == (0.5, {"MouthSmile": 0.4})
    # 超界强度先收敛再缩放；权重保留 4 位小数
    _, scaled_full = scaled_emotion_weights("tired", 2.0, table)
    assert scaled_full == {"relaxed": 0.6, "eye_close": 0.4}
    # 合法但档案未列的情绪（Warudo 档案缺档形态）按中性处理
    assert scaled_emotion_weights("neutral", 1.0, table) == (1.0, {})
    # 词表外情绪返回 None（调用方转失败结果）
    assert scaled_emotion_weights("joyful", 0.5, table) is None
