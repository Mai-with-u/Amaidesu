"""上下文分段计量器单测（src/modules/llm/context_meter.py）

覆盖：
- 分段估算（messages / mcp_tools / system_tools / skills / system）与条目明细形状
- 归属标注：MCP 与内置工具分段、技能读取结果与系统提示词片段记入技能段
- API 总数校准：分段等比缩放、总和与 api_prompt_tokens 一致
- 上游未回报（api_prompt_tokens=0）时展示原始估算值
- extract_request_sections 双路径（中立 payload / 遗留 kwargs）与缺失返回 None
- tokenizer 不可用时退化字符启发式（monkeypatch 隔离网络词表拉取）
- 多模态消息：图像 part 按固定估值计入

tiktoken 词表首次加载需联网拉取；单测通过 monkeypatch 计数函数或强制
启发式路径，保证离线可跑、不依赖缓存状态。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.modules.llm import context_meter as cm
from src.modules.llm.context_meter import (
    ContextBreakdown,
    estimate_breakdown,
    extract_request_sections,
)


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
        messages=messages if messages is not None else [{"role": "user", "content": "你好"}],
        tools=tools if tools is not None else [{"name": "reply", "description": "回复", "parameters": {}}],
        api_prompt_tokens=api_prompt_tokens,
    )


def _section(breakdown: ContextBreakdown, key: str) -> cm.ContextSection:
    return next(section for section in breakdown.sections if section.key == key)


@pytest.fixture(autouse=True)
def _force_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制启发式路径：单测不触碰 tiktoken 词表（可能未拉取）。"""
    monkeypatch.setattr(cm, "_get_encoding", lambda: None)


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
            {"role": "user", "content": "第一条"},
            {"role": "user", "content": "第二条"},
            {"role": "assistant", "content": "回复"},
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

    def test_no_api_total_keeps_raw_values(self) -> None:
        breakdown = _make_breakdown(api_prompt_tokens=0)
        assert breakdown.calibrated is False
        for section in breakdown.sections:
            assert section.tokens == section.raw_tokens

    def test_empty_request_produces_zero_sections(self) -> None:
        breakdown = estimate_breakdown(
            request_id="req_empty",
            profile_name="replyer",
            model_name="m",
            system="",
            messages=[],
            tools=[],
            api_prompt_tokens=0,
        )
        assert all(section.tokens == 0 for section in breakdown.sections)
        assert all(section.count == 0 for section in breakdown.sections)

    def test_image_part_counted_with_fixed_estimate(self) -> None:
        text_only = _make_breakdown(messages=[{"role": "user", "content": "看这里"}])
        with_image = _make_breakdown(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看这里"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
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
            {"role": "user", "content": "开局"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "minecraft_skill", "arguments": '{"name": "survival_opening"}'},
                    },
                    {"id": "c2", "name": "maicraft_perceive", "arguments": {"view": "situation"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "先砍树" * 20},
            {"role": "tool", "tool_call_id": "c2", "content": "附近有树"},
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


class TestExtractRequestSections:
    def test_payload_request_path(self) -> None:
        from src.modules.llm.payload import ContextPart, GenerateRequest, Message, ToolSpec

        request = GenerateRequest(
            messages=[Message(role="user", content="hi")],
            system="sys",
            tools=[ToolSpec(name="t", description="d"), ToolSpec(name="m", context_section="mcp_tools")],
            system_parts=[ContextPart(section="skills", name="技能目录", text="sys")],
        )
        sections = extract_request_sections({"request": request})
        assert sections is not None
        assert sections.system == "sys"
        # 中立 payload dump 形状：role 保留，文本在 parts
        assert sections.messages[0]["role"] == "user"
        assert "parts" in sections.messages[0]
        # 未设置的归属字段不进入计数用的 dump
        assert sections.tools[0] == {"name": "t", "description": "d", "parameters": {}}
        assert sections.tools[1]["context_section"] == "mcp_tools"
        assert sections.system_parts == [{"section": "skills", "name": "技能目录", "text": "sys"}]

    def test_legacy_kwargs_path(self) -> None:
        kwargs: Dict[str, Any] = {
            "messages": [{"role": "user", "content": "hi"}],
            "system": "sys",
            "tools": [{"type": "function", "function": {"name": "t"}}],
        }
        sections = extract_request_sections(kwargs)
        assert sections is not None
        assert sections.system == "sys"
        assert len(sections.tools) == 1
        assert sections.system_parts == []

    def test_missing_messages_returns_none(self) -> None:
        assert extract_request_sections({}) is None
        assert extract_request_sections({"messages": None}) is None


class TestHeuristicFallback:
    def test_cjk_text_positive_tokens(self) -> None:
        tokens = cm.count_text_tokens("这是一段中文测试文本")
        assert tokens > 0

    def test_empty_text_is_zero(self) -> None:
        assert cm.count_text_tokens("") == 0

    def test_heuristic_cjk_denser_than_ascii(self) -> None:
        cjk = cm._heuristic_tokens("一二三四五六七八九十" * 10)
        ascii_tokens = cm._heuristic_tokens("abcdefghij" * 10)
        assert cjk > ascii_tokens


class TestNormalizeToolSpec:
    def test_openai_style_function_dict(self) -> None:
        name, _dump = cm._normalize_tool_spec({"type": "function", "function": {"name": "reply"}})
        assert name == "reply"

    def test_neutral_dict(self) -> None:
        name, _dump = cm._normalize_tool_spec({"name": "speak"})
        assert name == "speak"

    def test_objects_fall_back_to_unknown(self) -> None:
        name, _ = cm._normalize_tool_spec(object())
        assert name == "unknown"
