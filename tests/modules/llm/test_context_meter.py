"""上下文分段计量：分段估算与明细、归属标注、API 总数校准、请求分段提取与离线启发式（不碰 tiktoken 词表）"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.llm import context_meter as cm
from src.modules.llm.context_meter import ContextBreakdown, estimate_breakdown, extract_request_sections
from src.modules.llm.payload import ContextPart, GenerateRequest, Message, ToolSpec


def _make_breakdown(
    *,
    system: str = "你是直播助手",
    messages: list | None = None,
    tools: list | None = None,
    api_prompt_tokens: int = 0,
) -> ContextBreakdown:
    return estimate_breakdown(
        request_id="req_test",
        profile_name="replyer",
        model_name="test-model",
        system=system,
        messages=messages if messages is not None else [{"role": "user", "parts": ["你好"]}],
        tools=tools if tools is not None else [{"name": "reply", "description": "回复", "parameters": {}}],
        api_prompt_tokens=api_prompt_tokens,
    )


def _section(breakdown: ContextBreakdown, key: str) -> cm.ContextSection:
    return next(section for section in breakdown.sections if section.key == key)


@pytest.fixture(autouse=True)
def _force_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制启发式路径：单测不触碰 tiktoken 词表（可能未拉取）。"""
    monkeypatch.setattr(cm, "get_encoding", lambda: None)


class TestEstimateBreakdown:
    def test_sections_present_with_counts(self) -> None:
        breakdown = _make_breakdown()
        keys = [section.key for section in breakdown.sections]
        assert keys == [
            cm.SECTION_MESSAGES,
            cm.SECTION_MCP_TOOLS,
            cm.SECTION_SYSTEM_TOOLS,
            cm.SECTION_SKILLS,
            cm.SECTION_SYSTEM,
        ]
        assert _section(breakdown, cm.SECTION_SYSTEM).count == 1
        assert _section(breakdown, cm.SECTION_MESSAGES).count == 1
        # 未标注归属的工具计入内置工具段
        assert _section(breakdown, cm.SECTION_SYSTEM_TOOLS).count == 1
        assert _section(breakdown, cm.SECTION_MCP_TOOLS).count == 0

    def test_tools_have_per_item_detail(self) -> None:
        tools = [
            {"name": "reply", "description": "回复观众", "parameters": {"type": "object"}},
            {"name": "speak", "description": "发声", "parameters": {"type": "object"}},
        ]
        breakdown = _make_breakdown(tools=tools)
        tool_section = _section(breakdown, cm.SECTION_SYSTEM_TOOLS)
        assert tool_section.count == 2
        assert [item.name for item in tool_section.items] == ["reply", "speak"]
        assert all(item.tokens > 0 for item in tool_section.items)

    def test_messages_grouped_by_role(self) -> None:
        messages = [
            {"role": "user", "parts": ["第一条"]},
            {"role": "user", "parts": ["第二条"]},
            {"role": "assistant", "parts": ["回复"]},
        ]
        breakdown = _make_breakdown(messages=messages)
        items = {item.name: item.tokens for item in _section(breakdown, cm.SECTION_MESSAGES).items}
        assert set(items) == {"user ×2", "assistant"}
        assert items["user ×2"] > items["assistant"]

    def test_calibration_scales_sections_to_api_total(self) -> None:
        breakdown = _make_breakdown(api_prompt_tokens=10_000)
        assert breakdown.calibrated is True
        assert breakdown.local_total_tokens > 0
        section_sum = sum(section.tokens for section in breakdown.sections)
        # 四舍五入允许 ±段数 的误差，量级必须与 API 总数一致
        assert abs(section_sum - 10_000) <= len(breakdown.sections)
        # 校准保持段间比例（原值比例与校准值比例近似一致）
        system, messages = _section(breakdown, cm.SECTION_SYSTEM), _section(breakdown, cm.SECTION_MESSAGES)
        if messages.raw_tokens > 0:
            raw_ratio = system.raw_tokens / messages.raw_tokens
            assert abs(system.tokens / messages.tokens - raw_ratio) < 0.05

    def test_without_api_total_keeps_raw_values(self) -> None:
        breakdown = _make_breakdown(api_prompt_tokens=0)
        assert breakdown.calibrated is False
        assert all(section.tokens == section.raw_tokens for section in breakdown.sections)
        empty = _make_breakdown(system="", messages=[], tools=[])
        assert all(section.tokens == 0 and section.count == 0 for section in empty.sections)

    def test_image_part_counted_with_fixed_estimate(self) -> None:
        text_only = _make_breakdown(messages=[{"role": "user", "parts": ["看这里"]}])
        with_image = _make_breakdown(
            messages=[
                {
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": "看这里"},
                        {"type": "image", "image": "https://example.com/x.png"},
                    ],
                }
            ]
        )
        assert (
            _section(with_image, cm.SECTION_MESSAGES).raw_tokens - _section(text_only, cm.SECTION_MESSAGES).raw_tokens
            == cm._IMAGE_TOKEN_ESTIMATE
        )


class TestAttribution:
    def test_mcp_and_system_tools_split_by_hint_and_hints_not_counted(self) -> None:
        tools = [
            {"name": "maicraft_perceive", "description": "感知", "parameters": {}, "context_section": "mcp_tools"},
            {"name": "minecraft_todo", "description": "待办", "parameters": {}, "context_section": "system_tools"},
            {"name": "legacy", "description": "未标注", "parameters": {}, "context_section": "bogus"},
        ]
        breakdown = _make_breakdown(tools=tools)

        assert [i.name for i in _section(breakdown, cm.SECTION_MCP_TOOLS).items] == ["maicraft_perceive"]
        # 不认识的归属按内置工具计
        assert [i.name for i in _section(breakdown, cm.SECTION_SYSTEM_TOOLS).items] == ["minecraft_todo", "legacy"]
        # 归属标注只供计量，不计入工具声明的 token
        plain = _make_breakdown(tools=[{"name": "maicraft_perceive", "description": "感知", "parameters": {}}])
        assert (
            _section(plain, cm.SECTION_SYSTEM_TOOLS).raw_tokens == _section(breakdown, cm.SECTION_MCP_TOOLS).raw_tokens
        )

    def test_skill_results_and_catalog_attributed_to_skills(self) -> None:
        catalog = "## 技能\n- survival\n  - survival_opening：开局"
        system = "你是 AI 玩家。\n\n" + catalog
        tools = [{"name": "minecraft_skill", "description": "读技能", "parameters": {}, "result_section": "skills"}]
        messages = [
            {"role": "user", "parts": ["开局"]},
            {
                "role": "assistant",
                "parts": [],
                "tool_calls": [
                    {"id": "c1", "name": "minecraft_skill", "arguments": {"name": "survival_opening"}},
                    {"id": "c2", "name": "maicraft_perceive", "arguments": {"view": "situation"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "parts": ["先砍树" * 20]},
            {"role": "tool", "tool_call_id": "c2", "parts": ["附近有树"]},
        ]
        common = dict(request_id="r", profile_name="minecraft", model_name="m", system=system, messages=messages)
        without_parts = cm.estimate_breakdown(tools=tools, **common)
        with_parts = cm.estimate_breakdown(
            tools=tools, system_parts=[{"section": "skills", "name": "技能目录", "text": catalog}], **common
        )

        skills = {i.name: i.tokens for i in _section(with_parts, cm.SECTION_SKILLS).items}
        assert set(skills) == {"survival_opening", "技能目录"}
        assert skills["技能目录"] == cm.count_text_tokens(catalog)
        # 目录从系统提示词段扣出，总量不变
        assert (
            _section(with_parts, cm.SECTION_SYSTEM).raw_tokens
            == _section(without_parts, cm.SECTION_SYSTEM).raw_tokens - skills["技能目录"]
        )
        assert with_parts.local_total_tokens == without_parts.local_total_tokens
        # 普通工具结果仍在对话消息段，按工具名列出；技能正文不留在消息段
        message_items = {i.name for i in _section(with_parts, cm.SECTION_MESSAGES).items}
        assert "tool · maicraft_perceive" in message_items
        assert not any("minecraft_skill" in name for name in message_items)

    def test_system_part_not_in_system_text_is_ignored(self) -> None:
        breakdown = cm.estimate_breakdown(
            request_id="r",
            profile_name="p",
            model_name="m",
            system="系统提示词",
            messages=[],
            tools=[],
            system_parts=[{"section": "skills", "name": "技能目录", "text": "不在系统提示词里"}],
        )
        assert _section(breakdown, cm.SECTION_SKILLS).raw_tokens == 0


def test_extract_request_sections_from_payload_request() -> None:
    """计量原值只从入站归一化后的中立请求提取，request 缺失返回 None。"""
    request = GenerateRequest(
        messages=[Message(role="user", parts=["hi"])],
        system="sys",
        tools=[ToolSpec(name="t", description="d"), ToolSpec(name="m", context_section="mcp_tools")],
        system_parts=[ContextPart(section="skills", name="技能目录", text="sys")],
    )
    sections = extract_request_sections({"request": request})
    assert sections.system == "sys"
    # 中立 payload dump：role 保留，文本在 parts；未设置的归属字段不进入计数用的 dump
    assert sections.messages[0]["role"] == "user" and "parts" in sections.messages[0]
    assert sections.tools[0] == {"name": "t", "description": "d", "parameters": {}}
    assert sections.tools[1]["context_section"] == "mcp_tools"
    assert sections.system_parts == [{"section": "skills", "name": "技能目录", "text": "sys"}]

    assert extract_request_sections({}) is None
    assert extract_request_sections({"messages": [{"role": "user", "parts": ["hi"]}]}) is None


def test_heuristic_token_counting() -> None:
    assert cm.count_text_tokens("这是一段中文测试文本") > 0
    assert cm.count_text_tokens("") == 0
    assert cm._heuristic_tokens("一二三四五六七八九十" * 10) > cm._heuristic_tokens("abcdefghij" * 10)


@pytest.mark.parametrize(
    ("spec", "name"),
    [
        ({"type": "function", "function": {"name": "reply"}}, "reply"),
        ({"name": "speak"}, "speak"),
        (object(), "unknown"),
    ],
)
def test_normalize_tool_spec_name(spec: Any, name: str) -> None:
    assert cm._normalize_tool_spec(spec)[0] == name
