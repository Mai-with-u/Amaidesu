"""主动折叠原语单测：编号、可折区间、就地折叠与拒绝、收起、取回、请模型的节奏。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

import pytest

from src.modules.context import (
    BLOCK_KEY,
    REF_KEY,
    ContextFolds,
    FoldPacer,
    FoldRequest,
    TokenCounter,
    group_messages,
    group_spans,
)

HINT = "原文可用 recall 取回"


@pytest.fixture(autouse=True)
def _chars_as_tokens(chars_as_tokens: None) -> None:
    """整个文件按字符数计 token：转用 tests/conftest.py 的共享夹具。"""


def _pinned(message: Mapping[str, Any]) -> bool:
    return message.get("role") == "system" or bool(message.get("_pinned"))


def _folds() -> ContextFolds:
    return ContextFolds(TokenCounter(), is_pinned=_pinned, recall_hint=HINT)


def _history(rounds: int = 6, size: int = 400) -> List[Dict[str, Any]]:
    """系统说明 + 一条固定指令 + 若干轮"调用 + 回执"，每份回执 ``size`` 字。"""
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": "去挖铁", "_pinned": True},
    ]
    for i in range(rounds):
        messages.append(
            {
                "role": "assistant",
                "content": f"第{i}步",
                "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "observe", "arguments": "{}"}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"回执{i}:" + "石" * size})
    return messages


def _refs(messages: List[Dict[str, Any]]) -> List[str]:
    return [message[REF_KEY] for message in messages if REF_KEY in message]


def _pairs_intact(messages: List[Dict[str, Any]]) -> bool:
    """每份回执都紧跟在发起它的调用之后。"""
    pending: set = set()
    for message in messages:
        if message.get("tool_calls"):
            pending = {call["id"] for call in message["tool_calls"]}
        elif message.get("role") == "tool":
            if message["tool_call_id"] not in pending:
                return False
        else:
            pending = set()
    return True


def test_group_spans_cut_like_group_messages() -> None:
    """按下标切组与按列表切组同一套规则：调用与其回执一组，其余每条一组。"""
    messages = _history(rounds=3)
    assert [messages[start:end] for start, end in group_spans(messages)] == group_messages(messages)


def test_number_writes_ref_once_into_new_messages_only() -> None:
    """只给没编号的 user / tool 消息编号，写进正文开头；固定层与助手消息不编号，再调一次不重复写。"""
    folds = _folds()
    messages = _history(rounds=2)
    assert folds.number(messages) == 2
    assert _refs(messages) == ["m0001", "m0002"]
    assert messages[3]["content"].startswith("[m0001] 回执0:")
    assert REF_KEY not in messages[1] and REF_KEY not in messages[2]
    before = [dict(message) for message in messages]
    messages.append({"role": "user", "content": [{"type": "image_ref", "ref": "img1"}, {"type": "text", "text": "看"}]})
    assert folds.number(messages) == 1
    assert messages[:-1] == before
    assert messages[-1]["content"][1]["text"] == "[m0003] 看"


def test_ref_numbers_keep_growing_after_clear() -> None:
    """开新任务清空块库，但编号继续往后数：旧任务的折叠提议对不上新任务的消息。"""
    folds = _folds()
    folds.number(_history(rounds=2))
    folds.clear()
    fresh = _history(rounds=1)
    folds.number(fresh)
    assert _refs(fresh) == ["m0003"]


def test_ranges_skip_pinned_and_recent_groups() -> None:
    """可折区间避开固定层和最近几组，列出起止编号、体积与调用过的工具。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    ranges = folds.ranges(messages, keep_recent=2, min_tokens=100)
    assert len(ranges) == 1
    assert (ranges[0].start, ranges[0].end) == ("m0001", "m0004")
    assert ranges[0].tools == ("observe×4",)
    assert not folds.ranges(messages, keep_recent=2, min_tokens=10_000)


def test_apply_replaces_whole_groups_and_keeps_original_for_recall() -> None:
    """从回执编号起折也会带上发起它的调用；原区间换成一条替身，原文按块号读得回来。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    outcome = folds.apply(
        messages,
        [FoldRequest("m0001", "m0003", "挖了三处：都是石头，没铁")],
        keep_recent=2,
        min_tokens=100,
        max_summary_chars=500,
    )
    assert not outcome.errors and len(outcome.blocks) == 1
    stand_in = messages[2]
    assert stand_in[BLOCK_KEY] == "b1" and stand_in["role"] == "user"
    assert stand_in["content"].startswith("[已折叠 b1 · m0001–m0003") and "没铁" in stand_in["content"]
    assert _pairs_intact(messages) and len(messages) == 2 + 1 + 6
    assert outcome.tokens_saved > 0
    recalled = folds.recall(block="b1", max_chars=100_000)
    assert recalled["ok"] and "回执2:" in recalled["original"] and "调用 observe" in recalled["original"]


def test_apply_on_earlier_snapshot_survives_tail_appends() -> None:
    """提议基于稍早的历史算出，期间尾部又追加了几轮：按编号重新定位照样折得上。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    snapshot_ranges = folds.ranges(list(messages), keep_recent=2, min_tokens=100)
    messages.extend(_history(rounds=2)[2:])
    folds.number(messages)
    request = FoldRequest(snapshot_ranges[0].start, snapshot_ranges[0].end, "前四步的观察都已用完")
    outcome = folds.apply(messages, [request], keep_recent=2, min_tokens=100, max_summary_chars=500)
    assert not outcome.errors and messages[2][BLOCK_KEY] == "b1" and _pairs_intact(messages)


def test_fold_can_swallow_an_earlier_block() -> None:
    """新区间可以从已有块号起折：旧块被新块吞并，旧块原文仍在块库里。"""
    folds = _folds()
    messages = _history(rounds=8)
    folds.number(messages)
    folds.apply(
        messages, [FoldRequest("m0001", "m0002", "前两步")], keep_recent=2, min_tokens=100, max_summary_chars=500
    )
    outcome = folds.apply(
        messages, [FoldRequest("b1", "m0004", "前四步")], keep_recent=2, min_tokens=100, max_summary_chars=500
    )
    assert not outcome.errors and messages[2][BLOCK_KEY] == "b2"
    assert "[已折叠 b1" in folds.recall(block="b2", max_chars=100_000)["original"]
    assert folds.recall(block="b1", max_chars=100_000)["ok"]


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (FoldRequest("m0099", "m0002", "摘要"), "编号不在"),
        (FoldRequest("m0003", "m0001", "摘要"), "起点在终点之后"),
        (FoldRequest("m0001", "m0002", "  "), "摘要为空"),
        (FoldRequest("m0001", "m0006", "摘要"), "不能折"),
        (FoldRequest("m0001", "m0001", "摘要"), "不值得折"),
        (FoldRequest("m0001", "m0002", "长" * 620), "净省不足"),
        (FoldRequest("m0001", "m0002", "长" * 900), "净省不足"),
    ],
)
def test_apply_refuses_bad_requests_and_keeps_history(request_: FoldRequest, reason: str) -> None:
    """编号不存在、起止颠倒、摘要为空、碰到最近几组、区间太小、或折后净省空间不足：一律拒绝且历史不动。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    before = [dict(message) for message in messages]
    outcome = folds.apply(messages, [request_], keep_recent=2, min_tokens=600, max_summary_chars=600)
    assert not outcome.blocks and reason in outcome.errors[0]
    assert messages == before and not folds.blocks


def test_apply_accepts_slightly_over_limit_summary_within_grace() -> None:
    """轻微超限缓冲（第 66 场 3008 字超 3000 字上限 8 字）：落在缓冲内且净省充足时全文入账，不整单作废也不截断。"""
    folds = _folds()
    messages = _history(rounds=8, size=1100)
    folds.number(messages)
    summary = "记" * 3008
    outcome = folds.apply(
        messages,
        [FoldRequest("m0001", "m0006", summary)],
        keep_recent=2,
        min_tokens=1000,
        max_summary_chars=3000,
    )
    assert not outcome.errors and len(outcome.blocks) == 1
    block = outcome.blocks[0]
    assert block.summary == summary
    assert "超长部分已截断" not in messages[2]["content"]
    assert block.tokens_after < block.tokens_before
    assert outcome.tokens_saved >= int(block.tokens_before * 0.4)


def test_apply_truncates_overlong_summary_at_sentence_or_newline_with_recall_note() -> None:
    """超出缓冲上限的长摘要：优先在后半段句号（含闭合引号）或换行处安全截断并附注取回方式，块库原文完整可查。"""
    # 句末标点（带右引号）处截断，丢掉末尾半截残句
    folds_sentence = _folds()
    messages_a = _history(rounds=8, size=1100)
    folds_sentence.number(messages_a)
    prefix_a = "矿洞记录：" + "石" * 2400 + "第 3 层报错“路径被流体阻挡。”"
    overlong_a = prefix_a + "后面这半句超出预算还没写完" + "尾" * 1200
    outcome_a = folds_sentence.apply(
        messages_a,
        [FoldRequest("m0001", "m0006", overlong_a)],
        keep_recent=2,
        min_tokens=1000,
        max_summary_chars=3000,
    )
    assert not outcome_a.errors and len(outcome_a.blocks) == 1
    saved_a = outcome_a.blocks[0].summary
    assert saved_a.endswith(f"第 3 层报错“路径被流体阻挡。”\n（超长部分已截断，{HINT}）")
    assert "后面这半句超出预算还没写完" not in saved_a
    assert len(saved_a) <= 3000
    assert folds_sentence.recall(block="b1", max_chars=100_000)["ok"]

    # 无句号的多行条目：在换行处干净截断
    folds_newline = _folds()
    messages_b = _history(rounds=8, size=1100)
    folds_newline.number(messages_b)
    line_block = "\n".join(f"- 条目{idx}：" + "木" * 180 for idx in range(14))
    overlong_b = line_block + "\n- 未完条目：" + "枝" * 1200
    outcome_b = folds_newline.apply(
        messages_b,
        [FoldRequest("m0001", "m0006", overlong_b)],
        keep_recent=2,
        min_tokens=1000,
        max_summary_chars=3000,
    )
    assert not outcome_b.errors and len(outcome_b.blocks) == 1
    saved_b = outcome_b.blocks[0].summary
    assert saved_b.endswith(f"\n（超长部分已截断，{HINT}）")
    assert "未完条目" not in saved_b


def test_apply_guardrail_rejects_summary_fatter_than_or_too_close_to_original() -> None:
    """配对副作用护栏：放宽字数后若摘要换算成替身消息比原区间还胖、或净省空间不足，必须拒收并保持历史不动。"""
    folds = _folds()
    messages = _history(rounds=6, size=400)
    folds.number(messages)
    before_snapshot = [dict(message) for message in messages]

    # 摘要 1200 字虽未超 max_summary_chars=3000，但 m0001–m0002 原区间仅约 860 token，折后反而更胖
    fatter = folds.apply(
        messages,
        [FoldRequest("m0001", "m0002", "胖" * 1200)],
        keep_recent=2,
        min_tokens=600,
        max_summary_chars=3000,
    )
    assert not fatter.blocks and "净省不足" in fatter.errors[0]
    assert messages == before_snapshot and not folds.blocks

    # 折后略小于原区间但净省不到四成（m0001–m0003 原区间约 1527 token，摘要 1000 字折后约 1060 token）
    meager = folds.apply(
        messages,
        [FoldRequest("m0001", "m0003", "近" * 1000)],
        keep_recent=2,
        min_tokens=600,
        max_summary_chars=3000,
    )
    assert not meager.blocks and "净省不足" in meager.errors[0]
    assert messages == before_snapshot and not folds.blocks


def test_overlapping_request_in_same_batch_is_refused_alone() -> None:
    """同批两段重叠时只拒后一段，前一段照常折。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    outcome = folds.apply(
        messages,
        [FoldRequest("m0001", "m0002", "前两步"), FoldRequest("m0002", "m0003", "又一遍")],
        keep_recent=2,
        min_tokens=100,
        max_summary_chars=500,
    )
    assert len(outcome.blocks) == 1 and "重叠" in outcome.errors[0]


def test_stow_swaps_oldest_large_records_until_target() -> None:
    """收起从最老的大条记录起换成一行占位，降到目标线就停；编号与调用配对不变，原文按块号可取回。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    counter = TokenCounter()
    total = counter.messages(messages)
    saved = folds.stow(messages, keep_recent=2, current_tokens=total, target_tokens=total - 700, min_tokens=200)
    assert 700 <= saved < 1300
    assert messages[3]["content"].startswith("[m0001] [原文已收起 b1")
    assert messages[3]["tool_call_id"] == "c0" and messages[3][REF_KEY] == "m0001"
    assert messages[5]["content"].startswith("[m0002] [原文已收起 b2") and "回执2:" in messages[7]["content"]
    assert "回执0:" in folds.recall(block="b1", max_chars=100_000)["original"]
    assert _pairs_intact(messages)


def test_stow_never_touches_pinned_or_recent_records() -> None:
    """固定层和最近几组再大也不收起；没东西可收时返回 0。"""
    folds = _folds()
    messages = _history(rounds=2)
    messages[1]["content"] = "指令" * 500
    folds.number(messages)
    assert folds.stow(messages, keep_recent=2, current_tokens=10_000, target_tokens=0, min_tokens=10) == 0


def test_recall_by_query_returns_snippets_across_blocks() -> None:
    """按关键词在整个块库里找，只回命中处前后的片段；找不到时写明没找到。"""
    folds = _folds()
    messages = _history(rounds=6)
    messages[5]["content"] = "回执1:" + "石" * 300 + "铁矿在 x=12 y=40 z=-7" + "石" * 300
    folds.number(messages)
    folds.apply(messages, [FoldRequest("m0001", "m0003", "找矿")], keep_recent=2, min_tokens=100, max_summary_chars=500)
    found = folds.recall(query="x=12 y=40", max_chars=100_000)
    assert found["ok"] and found["hits"][0]["block"] == "b1"
    assert "铁矿在 x=12 y=40" in found["hits"][0]["snippet"] and len(found["hits"][0]["snippet"]) < 400
    assert folds.recall(query="钻石", max_chars=100_000)["hits"] == []
    assert not folds.recall(max_chars=100)["ok"]
    assert not folds.recall(block="b9", max_chars=100)["ok"]


def test_recall_truncates_long_block_and_says_so() -> None:
    """整块原文超过上限时只给前一段，并说明原文多长、怎么只看相关部分。"""
    folds = _folds()
    messages = _history(rounds=6)
    folds.number(messages)
    folds.apply(messages, [FoldRequest("m0001", "m0003", "找矿")], keep_recent=2, min_tokens=100, max_summary_chars=500)
    recalled = folds.recall(block="b1", max_chars=200)
    assert len(recalled["original"]) == 200 and "query" in recalled["truncated"]


def test_pacer_waits_for_line_and_growth() -> None:
    """没到折叠线不请；请过一次后要再涨一截才请，避免模型刚说没东西可折又被叫来。"""
    pacer = FoldPacer()
    assert not pacer.due(900, line=1000, growth=200)
    assert pacer.due(1000, line=1000, growth=200)
    pacer.settle(1000)
    assert not pacer.due(1150, line=1000, growth=200)
    assert pacer.due(1200, line=1000, growth=200)
    pacer.reset()
    assert pacer.due(1000, line=1000, growth=200)


def test_pacer_baseline_follows_history_down() -> None:
    """历史被别的途径压小后，再涨一截从压小后的体积起算：不必涨回压小前的高位。"""
    pacer = FoldPacer()
    pacer.settle(129_000)
    # 集中整理把历史压到 4 万：还没到折叠线，不请
    assert not pacer.due(40_000, line=80_000, growth=20_000)
    # 涨过折叠线、比压小后的体积多出一截：该请了（按旧基线要等到 14.9 万）
    assert pacer.due(81_000, line=80_000, growth=20_000)
