"""text_adv 识别契约测试：question 模板渲染、结构化解析、假件 reader。

覆盖四类解析输入：正常格式 / 无选项纯正文 / 缺坐标选项 / 畸形格式，
以及模板经 PromptManager 渲染非空、reader 假件与注册表真件的读屏路径。
"""

from __future__ import annotations

import asyncio
from typing import List

from src.agents.text_adv.vlm import (
    TEXT_ADV_VLM_QUESTION_KEY,
    FakeVisionReader,
    RegistryVisionReader,
    build_question,
    parse_reply,
)
from src.modules.prompts import get_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation

# 正常回复：2 个选项 + 坐标（第二项用全角括号与全角逗号，考验容错）
REPLY_OK = "正文：\n今天的天气真好，微风拂面。\n选项：\n1. 去学校 (320, 240)\n2. 留在家里（960，480）\n"

# 无选项纯正文
REPLY_NO_OPTIONS = "正文：\n只有一段叙述文字。\n"

# 缺坐标的选项：第一项无坐标（不可点），第二项正常
REPLY_MISSING_COORD = "正文：\n正文文字。\n选项：\n1. 看不清位置的选项\n2. 清晰的选项 (100, 200)\n"

# 畸形格式：完全没有契约标记的乱序文本
REPLY_MALFORMED = "这是一段完全没有格式标记的乱序文本，随口说说而已。"


# ---------------------------------------------------------------------------
# build_question / 模板
# ---------------------------------------------------------------------------


def test_template_renders_nonempty_with_key_guidance() -> None:
    """模板经 PromptManager.render 非空且含正文/坐标关键引导。"""
    rendered = get_prompt_manager().render(TEXT_ADV_VLM_QUESTION_KEY, options_directive="")
    assert rendered.strip(), "模板渲染结果不应为空"
    assert "正文" in rendered
    assert "固定格式" in rendered
    # 选项段引导（含坐标要求）经变量注入后同样非空
    with_options = get_prompt_manager().render(TEXT_ADV_VLM_QUESTION_KEY, options_directive="选项：坐标引导")
    assert "选项" in with_options


def test_build_question_with_options_within_limit() -> None:
    """要求选项时 question 含选项引导与坐标要求，且不超过 500 字符上限。"""
    question = build_question(want_options=True)
    assert "正文" in question
    assert "选项" in question
    assert "坐标" in question
    assert 1 <= len(question) <= 500


def test_build_question_without_options_differs() -> None:
    """不要求选项时不含选项列表引导，但仍在长度上限内。"""
    question = build_question(want_options=False)
    assert "选项：" not in question
    assert 1 <= len(question) <= 500


# ---------------------------------------------------------------------------
# parse_reply
# ---------------------------------------------------------------------------


def test_parse_reply_ok_two_options() -> None:
    """正常格式：2 个选项 + 坐标，label/vlm_xy/clickable 全部解析无损。"""
    reading = parse_reply(REPLY_OK, sent_width=1280)
    assert reading.parse_ok
    assert reading.parse_error is None
    assert reading.text == "今天的天气真好，微风拂面。"
    assert len(reading.options) == 2
    first, second = reading.options
    assert first.label == "去学校"
    assert first.vlm_xy == (320, 240)
    assert first.clickable
    # 全角括号 + 全角逗号容错
    assert second.label == "留在家里"
    assert second.vlm_xy == (960, 480)
    assert second.clickable


def test_parse_reply_pure_text_no_options() -> None:
    """无选项纯正文：解析成功且 options 为空。"""
    reading = parse_reply(REPLY_NO_OPTIONS, sent_width=1280)
    assert reading.parse_ok
    assert reading.text == "只有一段叙述文字。"
    assert reading.options == []


def test_parse_reply_option_without_coord_not_clickable() -> None:
    """缺坐标的选项标 clickable=False 且 vlm_xy=None，绝不猜坐标。"""
    reading = parse_reply(REPLY_MISSING_COORD, sent_width=1280)
    assert reading.parse_ok
    first, second = reading.options
    assert first.label == "看不清位置的选项"
    assert first.vlm_xy is None
    assert not first.clickable
    assert second.vlm_xy == (100, 200)
    assert second.clickable


def test_parse_reply_coord_out_of_sent_width_invalid() -> None:
    """x 越出发送图像宽度（缩放后）的坐标视为无效 → 不可点。"""
    reply = "正文：\n正文。\n选项：\n1. 越界选项 (1280, 500)\n2. 界内选项 (100, 50)\n"
    reading = parse_reply(reply, sent_width=640)
    assert reading.parse_ok
    first, second = reading.options
    assert first.vlm_xy is None
    assert not first.clickable
    assert second.vlm_xy == (100, 50)
    assert second.clickable


def test_parse_reply_malformed_returns_failure_not_raise() -> None:
    """畸形格式：返回失败标志为真的结果，不抛异常、不产出错误坐标。"""
    reading = parse_reply(REPLY_MALFORMED, sent_width=1280)
    assert not reading.parse_ok
    assert reading.parse_error
    assert reading.options == []


def test_parse_reply_empty_is_failure() -> None:
    """空文本（VLM 失败降级）也返回可判定的失败结果。"""
    reading = parse_reply("", sent_width=1280)
    assert not reading.parse_ok
    assert reading.parse_error


# ---------------------------------------------------------------------------
# reader：假件与注册表真件
# ---------------------------------------------------------------------------


def test_fake_vision_reader_parses_injected_reply() -> None:
    """FakeVisionReader 返回按契约解析后的结构化结果，并记录调用。"""
    reader = FakeVisionReader(reply=REPLY_OK, sent_width=1280)
    reading = asyncio.run(reader.read_screen(want_options=True))
    assert reading.parse_ok
    assert len(reading.options) == 2
    assert reader.calls, "假件应记录读屏调用"
    assert "选项" in reader.calls[0]["question"]


def test_registry_vision_reader_extracts_text_and_sent_width() -> None:
    """真件经 ToolRegistry 调用后解析，携带原始回复与实际发送宽度。"""

    class StubRegistry:
        """记录 invocation 并返回预置 ToolExecutionResult 的最小桩。"""

        def __init__(self, result: ToolExecutionResult) -> None:
            self._result = result
            self.invocations: List[ToolInvocation] = []

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            self.invocations.append(invocation)
            return self._result

    result = ToolExecutionResult(
        tool_name="vision_look_at_screen",
        success=True,
        content=REPLY_OK,
        structured_content={
            "text": REPLY_OK,
            "width": 1280,
            "height": 720,
        },
    )
    registry = StubRegistry(result)
    reader = RegistryVisionReader(registry)  # type: ignore[arg-type]
    reading = asyncio.run(reader.read_screen(want_options=True))

    assert reading.parse_ok
    assert reading.raw_text == REPLY_OK
    assert reading.sent_width == 1280
    assert len(reading.options) == 2
    invocation = registry.invocations[0]
    assert invocation.tool_name == "vision_look_at_screen"
    assert invocation.arguments is not None
    assert invocation.arguments.get("question") == build_question(want_options=True)


def test_registry_vision_reader_tool_failure_is_decidable() -> None:
    """工具调用失败：返回失败标志为真的结果，不抛异常。"""

    class FailingRegistry:
        """invoke 一律返回失败 result 的最小桩。"""

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message="未知工具",
            )

    reader = RegistryVisionReader(FailingRegistry())  # type: ignore[arg-type]
    reading = asyncio.run(reader.read_screen(want_options=False))
    assert not reading.parse_ok
    assert reading.parse_error


def test_screen_reading_fields_exist() -> None:
    """ScreenReading 携带原始回复文本与实际发送宽度（供坐标反算）。"""
    reading = parse_reply(REPLY_OK, sent_width=1280)
    assert reading.raw_text == REPLY_OK
    assert reading.sent_width == 1280
    assert isinstance(reading.options[0].label, str)
    assert isinstance(reading.options[0].vlm_xy, tuple)
