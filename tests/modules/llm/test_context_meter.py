"""上下文分段计量器单测（src/modules/llm/context_meter.py）

覆盖：
- 三段估算（system / messages / tools）与条目明细形状
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


@pytest.fixture(autouse=True)
def _force_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制启发式路径：单测不触碰 tiktoken 词表（可能未拉取）。"""
    monkeypatch.setattr(cm, "_get_encoding", lambda: None)


class TestEstimateBreakdown:
    def test_three_sections_present_with_counts(self) -> None:
        breakdown = _make_breakdown()
        keys = [section.key for section in breakdown.sections]
        assert keys == [cm.SECTION_SYSTEM, cm.SECTION_MESSAGES, cm.SECTION_TOOLS]
        assert breakdown.sections[0].count == 1
        assert breakdown.sections[1].count == 1
        assert breakdown.sections[2].count == 1

    def test_tools_have_per_item_detail(self) -> None:
        tools = [
            {"name": "reply", "description": "回复观众", "parameters": {"type": "object"}},
            {"name": "speak", "description": "发声", "parameters": {"type": "object"}},
        ]
        breakdown = _make_breakdown(tools=tools)
        tool_section = breakdown.sections[2]
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
        items = {item.name: item.tokens for item in breakdown.sections[1].items}
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
        system, messages = breakdown.sections[0], breakdown.sections[1]
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
            with_image.sections[1].raw_tokens - text_only.sections[1].raw_tokens
            == cm._IMAGE_TOKEN_ESTIMATE
        )


class TestExtractRequestSections:
    def test_payload_request_path(self) -> None:
        from src.modules.llm.payload import GenerateRequest, Message, ToolSpec

        request = GenerateRequest(
            messages=[Message(role="user", content="hi")],
            system="sys",
            tools=[ToolSpec(name="t", description="d")],
        )
        sections = extract_request_sections({"request": request})
        assert sections is not None
        system, messages, tools = sections
        assert system == "sys"
        # 中立 payload dump 形状：role 保留，文本在 parts
        assert messages[0]["role"] == "user"
        assert "parts" in messages[0]
        assert tools == [{"name": "t", "description": "d", "parameters": {}}]

    def test_legacy_kwargs_path(self) -> None:
        kwargs: Dict[str, Any] = {
            "messages": [{"role": "user", "content": "hi"}],
            "system": "sys",
            "tools": [{"type": "function", "function": {"name": "t"}}],
        }
        sections = extract_request_sections(kwargs)
        assert sections is not None
        assert sections[0] == "sys"
        assert len(sections[2]) == 1

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
