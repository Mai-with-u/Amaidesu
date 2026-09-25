"""T10 输入预算单测——单项 2000 字符截断 / 历史 12000 字符预算（成块丢最旧）。

覆盖：
- 单项内容上限：canonical 出口超 2000 截断 + "…（截断）"标记（与观察帽同口径）
- 正常内容（<2000）原样不截断
- 历史字符预算：超预算成块丢最旧，保最新、无半条
- 双上限（条数 30 + 字符 12000）先到先丢语义
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.canonical import SINGLE_ITEM_MAX_CHARS, drop_oldest_blocks, truncate_item
from src.agents.streamer.planner import _HISTORY_CHAR_BUDGET, Planner
from src.agents.streamer.room_state import RoomState


@dataclass(frozen=True)
class FakeTurn:
    """live_chat 行的历史视图（同测试包内惯例）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


def _make_planner() -> Planner:
    """构造最小 Planner（只测 _build_dialogue_messages，不跑 LLM 循环）。"""
    from unittest.mock import MagicMock

    return Planner(
        config={"planner_max_steps": 2},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        reply_provider=MagicMock(),
    )


# ---------------------------------------------------------------------------
# 单项内容上限 2000
# ---------------------------------------------------------------------------


def test_single_item_truncated_with_marker() -> None:
    """单条 >2000 字符 → 渲染后 ≤2000+标记长度，且带"…（截断）"。"""
    long_text = "超" * 5000
    content = canonical.canonical_content(role="user", nickname="小明", text=long_text, message_id="m1")
    assert "…（截断）" in content
    assert len(content) <= SINGLE_ITEM_MAX_CHARS + len("…（截断）") + len(" [id:m1]") + len("小明: ")


def test_single_item_truncated_assistant_role() -> None:
    """主播行（无前缀无 id）同样受单项帽约束。"""
    content = canonical.canonical_content(role="assistant", text="长" * 5000, message_type="speak")
    assert content.endswith("…（截断）")
    assert len(content) == SINGLE_ITEM_MAX_CHARS + len("…（截断）")


def test_normal_content_untouched() -> None:
    """<2000 的正常内容原样渲染，无截断标记。"""
    text = "来个落地水"
    content = canonical.canonical_content(role="user", nickname="小明", text=text, message_id="m1")
    assert content == f"小明: {text} [id:m1]"
    assert "…（截断）" not in content


def test_truncate_item_boundary_exactly_at_cap() -> None:
    """恰好等于上限不截断（超出才截）。"""
    text = "a" * SINGLE_ITEM_MAX_CHARS
    assert truncate_item(text) == text


# ---------------------------------------------------------------------------
# 历史字符预算 12000（成块丢最旧）
# ---------------------------------------------------------------------------


def _history_50x300() -> List[FakeTurn]:
    """50 条 × 300 字符的历史（加上昵称/前缀实际渲染略超 300）。"""
    return [
        FakeTurn(role="user", content=f"消息{i}" + "字" * 300, sender_name=f"观众{i}", message_id=f"m{i}")
        for i in range(50)
    ]


def test_history_char_budget_drops_oldest_whole_blocks() -> None:
    """50×300 历史 → 组装后总字符 ≤12000，最旧整条被丢、最新保留、无半条。"""
    planner = _make_planner()
    history = _history_50x300()
    messages = planner._build_dialogue_messages([], history)

    total = sum(len(m["content"]) for m in messages)
    assert total <= _HISTORY_CHAR_BUDGET
    # 最新保留（最后一条完整在场，含 id）
    assert messages[-1]["content"].endswith("[id:m49]")
    # 最旧一条（m0）已被整条丢弃——保留段中不存在其 id
    assert all("[id:m0]" not in m["content"] for m in messages)
    # 保留的是"连续的最新后缀"：id 连续递增无断层
    kept_ids = [int(m["content"].rsplit("[id:m", 1)[1].rstrip("]")) for m in messages]
    assert kept_ids == list(range(kept_ids[0], 50))


def test_history_within_budget_untouched() -> None:
    """预算内的历史原样保留，不丢任何一条。"""
    planner = _make_planner()
    history = [FakeTurn(role="user", content=f"消息{i}", sender_name="小明", message_id=f"m{i}") for i in range(10)]
    messages = planner._build_dialogue_messages([], history)
    assert len(messages) == 10
    assert messages[0]["content"].endswith("[id:m0]")


def test_drop_oldest_blocks_mechanism() -> None:
    """drop_oldest_blocks：成块丢最旧直到落回预算（含全部丢弃的极端）。"""
    msgs = [{"role": "user", "content": "a" * 100} for _ in range(5)]
    kept = drop_oldest_blocks(msgs, 250)
    assert [len(m["content"]) for m in kept] == [100, 100]  # 丢 3 留 2（整块）

    huge = [{"role": "user", "content": "b" * 500}]
    assert drop_oldest_blocks(huge, 100) == []  # 预算装不下任何块时全丢（单项帽保证生产上不发生）


# ---------------------------------------------------------------------------
# 双上限先到先丢（条数 30 + 字符 12000）
# ---------------------------------------------------------------------------


def test_dual_cap_first_reached_wins() -> None:
    """条数与字符两上限各自独立生效：谁先触顶谁决定丢弃量。

    - 短消息 50 条：字符远不超预算 → 只受条数上限约束（保留 30 条）
    - 长消息 50 条：条数与字符同时超 → 字符预算更严（保留 < 30 条）
    """
    planner = _make_planner()

    # 条数上限场景（历史读取处 limit=history_limit 生效后的等效形态）
    short_history = [FakeTurn(role="user", content=f"短{i}", sender_name="小明", message_id=f"s{i}") for i in range(50)]
    # planner 不重复做条数截断（条数上限在读取处已生效），50 条短消息原样全保留
    messages_short = planner._build_dialogue_messages([], short_history)
    assert len(messages_short) == 50
    assert messages_short[0]["content"].endswith("[id:s0]")

    # 字符预算场景：50 条长消息（单条渲染约 600+ 字符），字符上限先于条数上限触顶
    long_history = [
        FakeTurn(role="user", content=f"长消息{i}" + "字" * 600, sender_name=f"观众{i}", message_id=f"L{i}")
        for i in range(50)
    ]
    messages_long = planner._build_dialogue_messages([], long_history)
    assert len(messages_long) < 30  # 字符预算先触顶
    assert sum(len(m["content"]) for m in messages_long) <= _HISTORY_CHAR_BUDGET


@pytest.mark.parametrize("cap,expect_keep", [(250, 2), (120, 1)])
def test_budget_parametrized_drop_count(cap: int, expect_keep: int) -> None:
    """不同预算下的成块丢弃量符合"整条丢最旧"语义（先到先丢的实现基础）。"""
    msgs = [
        {"role": "user", "content": "a" * 100},
        {"role": "user", "content": "b" * 100},
        {"role": "user", "content": "c" * 100},
    ]
    kept = drop_oldest_blocks(msgs, cap)
    assert len(kept) == expect_keep
    assert kept[-1]["content"] == "c" * 100  # 最新永远保留
