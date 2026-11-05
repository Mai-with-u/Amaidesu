"""滚动档案的取舍：长期有效的事先占位、新事优先、接过来的其余行补位，总数不超过上限。"""

from __future__ import annotations

from typing import List

from src.agents.streamer.memory.rolling import (
    MAX_LASTING_LINES,
    MAX_ROLLING_LINES,
    keep_from_base,
    render_base,
    select_rolling,
)
from src.modules.storage.models.memory import ArchiveLine, PendingArchiveLine


def _new(count: int, *, lasting: bool = False, start: int = 1) -> List[PendingArchiveLine]:
    return [
        PendingArchiveLine(text=f"新{index}", quote=f"新{index}", evidence_chat_id=index, lasting=lasting)
        for index in range(start, start + count)
    ]


def _kept(count: int, *, lasting: bool = False, start: int = 100) -> List[PendingArchiveLine]:
    return [
        PendingArchiveLine(
            text=f"旧{index}", quote=f"旧{index}", evidence_chat_id=index, from_session_id=1, lasting=lasting
        )
        for index in range(start, start + count)
    ]


def test_lasting_facts_are_not_pushed_out_by_a_busy_session() -> None:
    """一场新事写满 8 行、上一份还有 7 件长期的事和 3 件普通的事：长期的先占 6 行，新事补到 12 行，
    普通的旧事与第 7 件长期的事装不下。写入顺序是新事在前、接过来的在后。"""
    lasting = _kept(7, lasting=True)
    plain = _kept(3, start=200)
    fresh = _new(8)

    selected = select_rolling(fresh, [*lasting, *plain])

    assert len(selected) == MAX_ROLLING_LINES
    assert [line.text for line in selected] == [*(f"新{i}" for i in range(1, 7)), *(f"旧{i}" for i in range(100, 106))]
    assert sum(1 for line in selected if line.lasting) == MAX_LASTING_LINES


def test_lasting_new_fact_beyond_reserved_slots_still_counts_as_new() -> None:
    """长期的位子被接过来的占满时，本场新起的名字照样按新事排进来，不会因为是长期的反而落选。"""
    selected = select_rolling(_new(1, lasting=True), _kept(MAX_LASTING_LINES, lasting=True))

    assert [line.text for line in selected] == ["新1", *(f"旧{i}" for i in range(100, 106))]


def test_same_quote_kept_and_rewritten_keeps_only_the_new_line() -> None:
    """同一句原话既被接过来又被写成新行：只留新行。"""
    fresh = [PendingArchiveLine(text="炉子点上了", quote="点火", evidence_chat_id=5)]
    kept = [PendingArchiveLine(text="炉子没点火", quote="点火", evidence_chat_id=5, from_session_id=1)]

    assert [line.text for line in select_rolling(fresh, kept)] == ["炉子点上了"]


def _base() -> List[ArchiveLine]:
    return [
        ArchiveLine(
            id=1,
            live_session_id=9,
            text="Minecraft：女仆取名团子",
            quote="就叫团子",
            evidence_chat_id=11,
            game="minecraft",
            save_id="ab12cd34ef",
            save_name="新的世界",
            from_session_id=9,
            lasting=True,
        ),
        ArchiveLine(
            id=2, live_session_id=9, text="讲了鬼故事", quote="鬼故事讲完啦", evidence_chat_id=12, from_session_id=4
        ),
    ]


def test_base_lines_show_when_where_and_lasting() -> None:
    """给模型看的旧行：编号、上一场还是更早、标题、原话、在哪个游戏的哪个存档、是否长期。"""
    assert render_base(_base()).splitlines() == [
        "旧1 [上一场] Minecraft：女仆取名团子（原话「就叫团子」；在 minecraft「新的世界」；长期）",
        "旧2 [更早] 讲了鬼故事（原话「鬼故事讲完啦」）",
    ]
    assert render_base([]) == ""


def test_keep_accepts_numbers_and_old_prefix_and_inherits_lasting() -> None:
    """keep 里写编号、"旧N"或带 lasting 的对象都认；没给 lasting 沿用原标记；接过来的记着最初那一场。"""
    kept, missed = keep_from_base({"keep": ["旧2", {"old": 1}, 1, 9]}, _base())

    assert [(line.text, line.from_session_id, line.lasting) for line in kept] == [
        ("讲了鬼故事", 4, False),
        ("Minecraft：女仆取名团子", 9, True),
    ]
    assert [(item.what, item.reason) for item in missed] == [("9", "接着记的旧行编号不存在")]
    assert keep_from_base({"keep": [{"old": 1, "lasting": False}]}, _base())[0][0].lasting is False
    assert keep_from_base({"keep": None}, _base()) == ([], [])
