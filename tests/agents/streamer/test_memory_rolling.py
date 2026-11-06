"""滚动档案快照：模型对旧行的操作怎么落地、代码按什么固定规则取舍。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.agents.streamer.memory.rolling import (
    MAX_LASTING_LINES,
    MAX_SNAPSHOT_LINES,
    plan_from_ops,
    render_base,
    select_snapshot,
)
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import (
    ArchiveLine,
    ArchiveSnapshot,
    PendingArchiveLine,
    SnapshotLine,
    UnregisteredItem,
)

# 本场开播时刻：本场新写或再提起的行，"最近一次提起"记成它
_NOW = 9_000


def _old(index: int, *, session: int = 1, lasting: bool = False, seen: int = 1_000, **extra: Any) -> SnapshotLine:
    line = ArchiveLine(
        id=index, live_session_id=session, text=f"旧事{index}", quote=f"原话{index}", evidence_chat_id=index, **extra
    )
    return SnapshotLine(line=line, lasting=lasting, seen_at_ms=seen)


def _new(count: int, *, lasting: bool = False, start: int = 500) -> List[PendingArchiveLine]:
    return [
        PendingArchiveLine(text=f"新事{index}", quote=f"新{index}", evidence_chat_id=index, lasting=lasting)
        for index in range(start, start + count)
    ]


# 本场台词：只有第 1、2 行
_SPEECH = {
    1: SettleSpeechLine(id=71, timestamp_ms=9_100, content="那块菜地我不要了"),
    2: SettleSpeechLine(id=72, timestamp_ms=9_200, content="团子今天又帮我捡了一堆东西"),
}


def _line_of(raw: Any) -> Optional[SettleSpeechLine]:
    return _SPEECH.get(raw) if isinstance(raw, int) else None


def _validate(raw: Dict[str, Any]) -> Tuple[List[PendingArchiveLine], List[UnregisteredItem]]:
    """替身校验器：引文是那一行台词的子串才放行。"""
    line = _line_of(raw.get("line"))
    quote = str(raw.get("quote") or "")
    if line is None or not quote or quote not in line.content:
        return [], [UnregisteredItem(kind="archive", what=str(raw.get("title") or ""), quote=quote, reason="引文不对")]
    return [PendingArchiveLine(text=str(raw["title"]), quote=quote, evidence_chat_id=line.id)], []


def _plan(raw_ops: Any, base: List[SnapshotLine]):
    return plan_from_ops(raw_ops, base, session_started_at_ms=_NOW, line_of=_line_of, validate_new=_validate)


# ---------------------------------------------------------------------------
# 模型的操作
# ---------------------------------------------------------------------------


def test_no_ops_keeps_every_row_and_non_list_is_an_error() -> None:
    """输出里没有 previous：全部保留；previous 不是列表抛错（整理按失败处理，上一份照留）。"""
    base = [_old(1), _old(2)]
    assert _plan(None, base).kept == base
    assert _plan([], base).kept == base
    with pytest.raises(ValueError):
        _plan("全部", base)


def test_keep_marks_a_mention_and_retags_without_touching_the_row() -> None:
    """保留并指出本场又提到它的那一行：最近一次提起记成本场；重标稳定；档案行本身原样。"""
    base = [_old(1, seen=1_000), _old(2, seen=2_000)]

    plan = _plan([{"old": "旧1", "op": "keep", "line": 2, "lasting": True}, {"old": 2, "line": 99}], base)

    assert [(item.line, item.lasting, item.seen_at_ms) for item in plan.kept] == [
        (base[0].line, True, _NOW),
        (base[1].line, False, 2_000),
    ], "第 99 行不是本场台词，不算再提起"
    assert plan.changes == []


def test_drop_needs_a_reason_and_evidence_and_not_mentioned_is_not_a_reason() -> None:
    """去掉要写理由并指出本场证据；"这场没提到"不算理由；不采纳的旧行照留，理由进改动清单。"""
    base = [_old(1), _old(2), _old(3), _old(4)]

    plan = _plan(
        [
            {"old": 1, "op": "drop", "reason": "了结", "line": 1},
            {"old": 2, "op": "drop", "line": 1},
            {"old": 3, "op": "drop", "reason": "这一场没再提起", "line": 1},
            {"old": 4, "op": "drop", "reason": "了结"},
        ],
        base,
    )

    assert [item.line.id for item in plan.kept] == [2, 3, 4]
    assert [(change.action, change.what) for change in plan.changes] == [
        ("drop", "旧事1"),
        ("refused", "旧事2"),
        ("refused", "旧事3"),
        ("refused", "旧事4"),
    ]
    assert plan.changes[0].quote == "那块菜地我不要了"


def test_replace_goes_through_the_validator_and_only_then_removes_the_old_row() -> None:
    """替换的新行引文对得上才换掉旧行；对不上的旧行照留，新行进未登记清单。"""
    base = [_old(1), _old(2)]

    plan = _plan(
        [
            {"old": 1, "op": "replace", "line": 2, "title": "团子会捡东西", "quote": "帮我捡了一堆东西"},
            {"old": 2, "op": "replace", "line": 2, "title": "编出来的", "quote": "这句她没说过"},
        ],
        base,
    )

    assert [item.line.id for item in plan.kept] == [2]
    assert [(line.text, line.evidence_chat_id) for line in plan.replacements] == [("团子会捡东西", 72)]
    assert [change.action for change in plan.changes] == ["replace", "refused"]
    assert [item.what for item in plan.misses] == ["编出来的"]


def test_unknown_numbers_repeated_rows_and_unknown_ops_are_refused() -> None:
    """认不出的编号、同一行第二个操作、看不懂的操作都不采纳，旧行照留。"""
    base = [_old(1)]

    plan = _plan(
        [{"old": 7, "op": "drop"}, {"old": 1, "op": "keep"}, {"old": 1, "op": "drop"}, "乱写", {"old": 0}],
        base,
    )

    assert [item.line.id for item in plan.kept] == [1]
    assert [change.reason for change in plan.changes] == [
        "旧行编号不存在",
        "同一行给了不止一个操作，只认第一个",
        "旧行编号不存在",
    ]


# ---------------------------------------------------------------------------
# 固定的取舍规则
# ---------------------------------------------------------------------------


def test_everything_fits_new_rows_first_then_kept_in_place() -> None:
    """装得下就全留：新事都进快照，接住的行按原来的位置跟在后面。"""
    kept = [_old(1), _old(2, lasting=True)]

    selection = select_snapshot(_new(2), kept, session_started_at_ms=_NOW)

    assert [line.in_snapshot for line in selection.lines] == [True, True]
    assert [item.archive_line_id for item in selection.kept] == [1, 2]
    assert selection.evicted == []


def test_over_the_cap_the_least_recently_mentioned_go_first_and_stable_facts_stay() -> None:
    """一场新事写满 8 行，上一份有 3 件稳定的事和 6 件普通的事：稳定的先留，普通旧事里最久没被再提的
    先被挤出；挤出的写进改动清单。"""
    stable = [_old(index, lasting=True, seen=1_000) for index in (1, 2, 3)]
    plain = [_old(index, seen=1_000 + index * 100) for index in range(4, 10)]

    selection = select_snapshot(_new(8), [*stable, *plain], session_started_at_ms=_NOW)

    assert all(line.in_snapshot for line in selection.lines)
    assert [item.archive_line_id for item in selection.kept] == [1, 2, 3, 9]
    assert len(selection.lines) + len(selection.kept) == MAX_SNAPSHOT_LINES
    assert [change.what for change in selection.evicted] == [f"旧事{index}" for index in range(4, 9)]


def test_stable_facts_take_at_most_six_rows_and_the_rest_compete_by_recency() -> None:
    """稳定的事至多先占 6 行（最近被提起的先占），多出来的与其余行一起按最近被提起排；
    本场新事比它们都新，超过上限的新事写成本场档案行但不进快照。"""
    stable = [_old(index, lasting=True, seen=index * 100) for index in range(1, 9)]

    selection = select_snapshot(_new(8), stable, session_started_at_ms=_NOW)

    assert [item.archive_line_id for item in selection.kept] == [3, 4, 5, 6, 7, 8]
    assert len(selection.kept) == MAX_LASTING_LINES
    assert [line.in_snapshot for line in selection.lines] == [True] * 6 + [False] * 2
    assert [change.what for change in selection.evicted] == ["新事506", "新事507", "旧事1", "旧事2"]


# ---------------------------------------------------------------------------
# 给模型看的旧行清单
# ---------------------------------------------------------------------------


def test_render_base_marks_previous_and_earlier_rows_with_save_and_stable_flag() -> None:
    """快照所属那一场的行写"上一场"，更早的写"之前"；挂游戏的写上存档，稳定的事标出来。"""
    snapshot = ArchiveSnapshot(
        owner_session_id=2,
        lines=[
            _old(1, session=2, game="minecraft", save_id="abcdef1234", save_name="新的世界"),
            _old(2, session=1, lasting=True, game="minecraft", save_id="abcdef1234"),
            _old(3, session=1),
        ],
    )

    assert render_base(snapshot).splitlines() == [
        "旧1 [上一场] 旧事1（原话「原话1」；在 minecraft「新的世界」）",
        "旧2 [之前] 旧事2（原话「原话2」；在 minecraft「abcdef12」；稳定）",
        "旧3 [之前] 旧事3（原话「原话3」）",
    ]
    assert render_base(None) == ""
