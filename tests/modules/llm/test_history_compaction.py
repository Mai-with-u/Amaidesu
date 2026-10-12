"""历史压缩机制单测：预算边界、摘要结构、首条角色合法性、回执优先。

覆盖 ``src.modules.llm.history_compaction`` 的压缩器纯函数行为与
配置字段（``history_token_budget``）的装配透传。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.modules.config.model_schemas import DEFAULT_HISTORY_TOKEN_BUDGET, LLMProfileConfig
from src.modules.llm.history_compaction import (
    SUMMARY_MARKER,
    HistoryCompactor,
    estimate_history_tokens,
    extractive_summary,
)


def _user(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": text}


def _assistant(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


def _chat_history(count: int, prefix: str = "闲聊") -> List[Dict[str, Any]]:
    """构造 user/assistant 交替的闲聊历史（不含关键事实词，压缩时可安全丢弃）。"""
    messages: List[Dict[str, Any]] = []
    for index in range(count):
        messages.append(_user(f"{prefix}消息{index} " + "内容" * 40))
        messages.append(_assistant(f"{prefix}回应{index} " + "哈哈" * 40))
    return messages


class TestBudgetBoundary:
    def test_within_budget_returns_same_object(self):
        """预算内原样返回同一对象（调用方以 is 判断未压缩，零开销路径）。"""
        messages = _chat_history(3)
        compactor = HistoryCompactor()
        assert compactor.compact(messages, 10**9) is messages

    def test_zero_budget_disables_compaction(self):
        """预算 0 = 不启用，无论历史多长都不压缩。"""
        messages = _chat_history(20)
        compactor = HistoryCompactor()
        assert compactor.compact(messages, 0) is messages

    def test_over_budget_triggers_compression(self):
        """超预算触发压缩：结果变短、出现摘要消息、近期消息保留。"""
        messages = _chat_history(30)
        # 预算只够装约一半历史
        budget = estimate_history_tokens(messages) // 2
        result = HistoryCompactor().compact(messages, budget, profile_name="planner")
        assert result is not messages
        assert estimate_history_tokens(result) < estimate_history_tokens(messages)
        summaries = [m for m in result if isinstance(m.get("content"), str) and SUMMARY_MARKER in m["content"]]
        assert len(summaries) == 1
        # 最近一组对话必须还在
        assert result[-1] == messages[-1]
        assert result[-2] == messages[-2]

    def test_estimation_matches_message_growth(self):
        """token 估算随文本增长单调增加（预算判断的输入可靠性）。"""
        short = estimate_history_tokens([_user("hi")])
        long = estimate_history_tokens([_user("hi" * 500)])
        assert 0 < short < long


class TestSummaryStructure:
    def test_summary_message_is_single_user_message(self):
        """压缩产物：恰一条 user 角色摘要消息，携带压缩条数说明与关键事实。"""
        messages = [
            _user("你今天答应过我带我打末影龙，还差多少准备？"),
            _assistant("我保证打完下界就去，目标是今晚。"),
            *_chat_history(10),
        ]
        budget = estimate_history_tokens(messages) // 3
        result = HistoryCompactor().compact(messages, budget)
        summaries = [m for m in result if SUMMARY_MARKER in str(m.get("content", ""))]
        assert len(summaries) == 1
        assert summaries[0]["role"] == "user"
        assert "答应" in summaries[0]["content"]

    def test_extractive_summary_covers_receipts_and_questions(self):
        """抽取摘要：回执记条数与检索提示，疑问与承诺行保留。"""
        source = [
            _user("直播间的下一个目标是什么？"),
            _assistant("我承诺先把房子盖完。"),
            {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"},
            {"role": "tool", "tool_call_id": "c2", "content": "{\"ok\": false}"},
        ]
        summary = extractive_summary(source)
        assert "2 条工具回执" in summary
        assert "分页检索" in summary
        assert "承诺" in summary
        assert "目标" in summary
        assert len(summary) <= 1400

    def test_summary_never_empty(self):
        """全寒暄历史也给兜底摘要，不产生空正文消息。"""
        summary = extractive_summary([_user("哈哈"), _assistant("嘿嘿")])
        assert summary.strip()

    def test_custom_summarizer_injected(self):
        """摘要函数可注入替换（LLM 摘要的后续接入点）。"""
        seen: List[List[Dict[str, Any]]] = []
        compactor = HistoryCompactor(summarizer=lambda msgs: (seen.append(msgs), "固定摘要")[1])
        messages = _chat_history(10)
        result = compactor.compact(messages, estimate_history_tokens(messages) // 2)
        assert seen, "注入的摘要函数应被调用"
        assert any("固定摘要" in str(m.get("content")) for m in result)


class TestFirstMessageLegality:
    def test_history_starting_with_assistant_becomes_legal(self):
        """以 assistant 开头的历史（旧硬切病灶）压缩后首条对话消息必须是 user。"""
        messages = [_assistant("（被切断的旧回应）" + "嗯" * 200), *_chat_history(10)]
        budget = estimate_history_tokens(messages) // 2
        result = HistoryCompactor().compact(messages, budget)
        conversational = [m for m in result if m.get("role") != "system"]
        assert conversational[0]["role"] == "user"
        assert SUMMARY_MARKER in conversational[0]["content"]

    def test_receipt_only_compression_still_prepends_user_summary(self):
        """只压回执时也前置 user 摘要消息，保证 assistant 开头不会残留。"""
        messages = [
            _assistant("我先看看四周。"),
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "look"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "{\"blocks\": []}"},
            _user("继续"),
        ]
        # 预算刚好装不下两条回执之外的内容：回执先被压
        budget = estimate_history_tokens(messages) - estimate_history_tokens([{"role": "tool", "tool_call_id": "c1", "content": "{\"blocks\": []}"}]) - 1
        result = HistoryCompactor().compact(messages, budget)
        conversational = [m for m in result if m.get("role") != "system"]
        assert conversational[0]["role"] == "user"

    def test_leading_system_messages_preserved(self):
        """前导 system 消息不属于对话历史，原样保留在最前。"""
        system = {"role": "system", "content": "固定系统说明"}
        messages = [system, _assistant("旧话" * 100), *_chat_history(10)]
        budget = estimate_history_tokens(messages) // 2
        result = HistoryCompactor().compact(messages, budget)
        assert result[0] is system
        conversational = [m for m in result if m.get("role") != "system"]
        assert conversational[0]["role"] == "user"


class TestReceiptPriority:
    def test_orphan_receipts_compressed_before_old_chat(self):
        """悬空回执组优先消化：预算只差一点时，回执先被压、旧对话保留。"""
        old_chat = [
            _user("最早的约定：先盖房子再挖矿。" + "背景" * 100),
            _assistant("好。" + "背景" * 100),
        ]
        orphan_receipts = [
            {"role": "tool", "tool_call_id": f"orphan{i}", "content": f"回执{i}：" + "内容" * 60}
            for i in range(2)
        ]
        messages = [
            *old_chat,
            orphan_receipts[0],
            _user("中间的闲聊" + "背景" * 100),
            orphan_receipts[1],
            _user("现在怎么办？"),
        ]
        # 预算 = 全部 - 最老一条回执的体积：必须腾出约一条回执的空间
        budget = estimate_history_tokens(messages) - estimate_history_tokens([orphan_receipts[0]])
        result = HistoryCompactor().compact(messages, budget)
        contents = [str(m.get("content", "")) for m in result]
        # 最老的回执被压（优先于旧对话）
        assert not any(content == orphan_receipts[0]["content"] for content in contents)
        # 旧对话的关键事实原样保留
        assert old_chat[0] in result

    def test_call_group_compressed_before_later_chat(self):
        """调用+回执捆绑组按从老到新压缩：最老的整组先消失，近期对话保留。"""
        calls = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "dig"}}]}
            for i in range(4)
        ]
        receipts = [
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"回执{i}：" + "内容" * 60} for i in range(4)
        ]
        messages = [_user("开场")]
        for call, receipt in zip(calls, receipts, strict=True):
            messages.extend([call, receipt])
        messages.append(_user("现在怎么办？"))

        budget = estimate_history_tokens(messages) // 2
        result = HistoryCompactor().compact(messages, budget)
        contents = [str(m.get("content", "")) for m in result]
        # 最老的回执被压
        assert not any(content == receipts[0]["content"] for content in contents)
        # 最新一组保留
        assert messages[-1] == result[-1]

    def test_tool_call_group_never_split(self):
        """assistant 调用与其回执整组同进退，不产生悬空调用。"""
        call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "dig"}}]}
        receipt = {"role": "tool", "tool_call_id": "c1", "content": "回执" * 200}
        messages = [_user("开场"), call, receipt, *_chat_history(10)]
        budget = estimate_history_tokens(messages) // 2
        result = HistoryCompactor().compact(messages, budget)
        call_ids_in_calls = {
            tc["id"] for m in result for tc in (m.get("tool_calls") or [])
        }
        receipt_ids = {m.get("tool_call_id") for m in result if m.get("role") == "tool"}
        assert call_ids_in_calls == receipt_ids, "调用与回执必须成对出现"


class TestConfigPlumbing:
    def test_profile_schema_default_budget(self):
        """profile 配置缺省携带历史 token 预算（存量文件加载即生效）。"""
        assert LLMProfileConfig().history_token_budget == DEFAULT_HISTORY_TOKEN_BUDGET
        assert LLMProfileConfig(history_token_budget=0).history_token_budget == 0

    def test_bootstrap_resolves_budget(self):
        """装配快照透传 history_token_budget；缺失的 toml 键取 schema 缺省。"""
        from src.modules.llm.bootstrap import build_resolved_profile

        models = {"default": ({"name": "default", "model_identifier": "m"}, "p")}
        resolved = build_resolved_profile(
            "planner", {"model_list": ["default"]}, {"default": models["default"]}
        )
        assert resolved.history_token_budget == DEFAULT_HISTORY_TOKEN_BUDGET
        resolved_zero = build_resolved_profile(
            "planner", {"model_list": ["default"], "history_token_budget": 0}, {"default": models["default"]}
        )
        assert resolved_zero.history_token_budget == 0

    def test_manager_query_returns_budget(self):
        """LLMManager 查询接口返回 profile 预算；未配置 profile 返回 0 不硬错。"""
        from src.modules.llm.engine import LLMManager

        config = {
            "llm_providers": [{"name": "p", "client_type": "openai", "base_url": "http://x", "api_key": "k"}],
            "llm_models": [{"name": "default", "api_provider": "p", "model_identifier": "m"}],
            "llm_profiles": {
                "planner": {"model_list": ["default"], "history_token_budget": 4321},
            },
        }
        manager = LLMManager()
        import asyncio

        asyncio.run(manager.setup(config))
        assert manager.get_history_token_budget("planner") == 4321
        assert manager.get_history_token_budget("no_such_profile") == 0

    def test_planner_compacts_history_over_budget(self):
        """Planner 历史构建接预算压缩：超预算时首条消息是 user 摘要。"""
        from unittest.mock import MagicMock

        from src.agents.streamer.planner import Planner

        llm = MagicMock()
        llm.get_history_token_budget = lambda profile: 500
        planner = Planner(config=None, llm_service=llm, prompt_service=MagicMock(), room_state=MagicMock())

        class _Turn:
            def __init__(self, role: str, content: str) -> None:
                self.role = role
                self.content = content
                self.sender_name = "观众"
                self.message_type = "danmaku"
                self.message_id = f"m{id(self)}"

        history = []
        for index in range(30):
            history.append(_Turn("user", f"弹幕{index} " + "内容" * 40))
            history.append(_Turn("assistant", f"回应{index} " + "哈哈" * 40))
        messages = planner._build_dialogue_messages([], history)
        assert messages[0]["role"] == "user"
        assert SUMMARY_MARKER in messages[0]["content"]
        # 最近一轮对话原样保留在尾部
        assert messages[-1]["content"].startswith("回应29")

    def test_replyer_compacts_history_over_budget(self):
        """Replyer 历史构建接预算压缩：超预算时历史段首条是 user 摘要。"""
        from unittest.mock import MagicMock

        from src.agents.streamer.replyer import Replyer

        llm = MagicMock()
        llm.get_history_token_budget = lambda profile: 500
        replyer = Replyer(config={}, llm_service=llm, prompt_service=MagicMock())
        history = []
        for index in range(30):
            history.append(type("T", (), {"role": "user", "content": f"弹幕{index} " + "内容" * 40})())
            history.append(type("T", (), {"role": "assistant", "content": f"回应{index} " + "哈哈" * 40})())
        # 与 canonical.turn_to_message 产出的 dict 序列同形
        history_messages = [{"role": t.role, "content": t.content} for t in history]
        compacted = replyer._compact_history(history_messages)
        assert compacted[0]["role"] == "user"
        assert SUMMARY_MARKER in compacted[0]["content"]


@pytest.mark.parametrize(
    "budget,expect_compacted",
    [
        (10**9, False),
        (0, False),
        (1, True),
    ],
)
def test_budget_parametrized(budget: int, expect_compacted: bool):
    """预算参数边界：无穷大与 0 不压缩，1 token 必压缩。"""
    messages = _chat_history(5)
    result = HistoryCompactor().compact(messages, budget)
    assert (result is not messages) is expect_compacted
