"""滚动档案快照：模型对旧行的操作怎么落地、代码按什么固定规则取舍。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.agents.streamer.memory.rolling import (
    MAX_BASE_LINES,
    MAX_HIGHLIGHT_QUOTA,
    MAX_LASTING_LINES,
    MAX_SESSION_HIGHLIGHTS,
    MAX_SNAPSHOT_LINES,
    plan_from_ops,
    render_base,
    select_snapshot,
)
from src.agents.streamer.memory.settle_proof import mark_unverified
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


def _old(
    index: int,
    *,
    session: int = 1,
    lasting: bool = False,
    seen: int = 1_000,
    pinned: bool = False,
    highlight: bool = False,
    **extra: Any,
) -> SnapshotLine:
    line = ArchiveLine(
        id=index,
        live_session_id=session,
        text=f"旧事{index}",
        quote=f"原话{index}",
        evidence_chat_id=index,
        highlight=highlight,
        **extra,
    )
    return SnapshotLine(line=line, lasting=lasting, seen_at_ms=seen, pinned=pinned, highlight=highlight)


def _new(
    count: int,
    *,
    lasting: bool = False,
    highlight: bool = False,
    start: int = 500,
    game: Optional[str] = None,
    save_id: str = "",
    prefix: str = "新事",
) -> List[PendingArchiveLine]:
    return [
        PendingArchiveLine(
            text=f"{prefix}{index}",
            quote=f"新{index}",
            evidence_chat_id=index,
            lasting=lasting,
            highlight=highlight,
            game=game,
            save_id=save_id,
        )
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
    """去掉要写理由并指出本场证据；理由要以了结、不成立、记错、被取代四类之一开头，"这场没提到"
    "一次性遭遇"都不算，"还没了结"这种只是含有类别词的也不算；不采纳的旧行照留，理由进改动清单。"""
    base = [_old(1), _old(2), _old(3), _old(4), _old(5), _old(6), _old(7)]

    plan = _plan(
        [
            {"old": 1, "op": "drop", "reason": "了结", "line": 1},
            {"old": 2, "op": "drop", "line": 1},
            {"old": 3, "op": "drop", "reason": "这一场没再提起", "line": 1},
            {"old": 4, "op": "drop", "reason": "了结"},
            {"old": 5, "op": "drop", "reason": "一次性遭遇，不用再记", "line": 1},
            {"old": 6, "op": "drop", "reason": "被取代：她说换了一块地", "line": 1},
            {"old": 7, "op": "drop", "reason": "这件事还没了结", "line": 1},
        ],
        base,
    )

    assert [item.line.id for item in plan.kept] == [2, 3, 4, 5, 7]
    assert [(change.action, change.what, change.reason) for change in plan.changes] == [
        ("drop", "旧事1", "了结"),
        ("refused", "旧事2", "去掉旧行要写理由并指出本场哪一行台词作证据，旧行保留"),
        ("refused", "旧事3", "这场没提到不算去掉的理由，旧行保留"),
        ("refused", "旧事4", "去掉旧行要写理由并指出本场哪一行台词作证据，旧行保留"),
        ("refused", "旧事5", "去掉的理由要以了结、不成立、记错或被取代开头，旧行保留"),
        ("drop", "旧事6", "被取代：她说换了一块地"),
        ("refused", "旧事7", "去掉的理由要以了结、不成立、记错或被取代开头，旧行保留"),
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


def test_duplicate_row_can_go_without_evidence_and_survivor_inherits_stable_flag() -> None:
    """同一件事写了两行：注明与哪一行重复就能去掉一行（不需要本场证据），留下的那一行继承"稳定"；
    重复的那一行本场被去掉了，这一行跟着去掉；注明与自己重复的不采纳。"""
    base = [_old(1, lasting=True), _old(2), _old(3), _old(4)]

    plan = _plan(
        [
            {"old": 1, "op": "drop", "same_as": 2},
            {"old": 3, "op": "drop", "same_as": "旧4"},
            {"old": 4, "op": "drop", "reason": "了结", "line": 1},
            {"old": 2, "op": "drop", "same_as": 2},
        ],
        base,
    )

    assert [(item.line.id, item.lasting) for item in plan.kept] == [(2, True)]
    assert [(change.action, change.what) for change in plan.changes] == [
        ("drop", "旧事4"),
        ("refused", "旧事2"),
        ("drop", "旧事1"),
        ("drop", "旧事3"),
    ]
    assert plan.changes[3].reason == "与旧4重复（旧4本场已被替换或去掉）"


def test_two_rows_naming_each_other_as_duplicates_keep_one() -> None:
    """两行互相注明与对方重复：先落地的那条去掉一行，后一条不采纳，事实由留下的那一行接着、继承"稳定"。"""
    base = [_old(1, lasting=True), _old(2)]

    plan = _plan([{"old": 1, "op": "drop", "same_as": 2}, {"old": 2, "op": "drop", "same_as": 1}], base)

    assert [(item.line.id, item.lasting) for item in plan.kept] == [(2, True)]
    assert [(change.action, change.what, change.reason) for change in plan.changes] == [
        ("drop", "旧事1", "与旧2重复"),
        ("refused", "旧事2", "注明重复的那一行也按重复去掉了，旧行保留"),
    ]


def test_duplicates_in_a_cycle_never_remove_every_row() -> None:
    """A→B→C→A 绕成一圈：前两条依次去掉，最后一条指向已按重复去掉的行，不采纳；圈里至少留下一行，
    "稳定"沿着去掉的方向传给留下的那一行。"""
    base = [_old(1, lasting=True), _old(2), _old(3)]

    plan = _plan(
        [
            {"old": 1, "op": "drop", "same_as": 2},
            {"old": 2, "op": "drop", "same_as": 3},
            {"old": 3, "op": "drop", "same_as": 1},
        ],
        base,
    )

    assert [(item.line.id, item.lasting) for item in plan.kept] == [(3, True)]
    assert [change.action for change in plan.changes] == ["drop", "drop", "refused"]


def test_replacement_inherits_the_stable_flag_unless_the_model_says_otherwise() -> None:
    """替换掉稳定的旧行时新行也算稳定；模型明确写 lasting:false 的照它；替换普通旧行的仍是普通行。"""
    base = [_old(1, lasting=True), _old(2, lasting=True), _old(3)]

    plan = _plan(
        [
            {"old": 1, "op": "replace", "line": 2, "title": "团子会捡东西", "quote": "帮我捡了一堆东西"},
            {"old": 2, "op": "replace", "line": 2, "title": "团子又捡东西", "quote": "团子今天", "lasting": False},
            {"old": 3, "op": "replace", "line": 1, "title": "菜地不要了", "quote": "那块菜地"},
        ],
        base,
    )

    assert [(line.text, line.lasting) for line in plan.replacements] == [
        ("团子会捡东西", True),
        ("团子又捡东西", False),
        ("菜地不要了", False),
    ]


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
    """分区各自计额：游戏世界里 10 件取名（稳定）全留，且完全不占用最近事件名额；
    本场 6 件新事件全留，前几场 8 件普通旧事按最近被提起保留最近 6 件，最久没提的 2 件先挤出。"""
    names = [
        SnapshotLine(
            line=ArchiveLine(
                id=index,
                live_session_id=1,
                text=f"Minecraft：宠物{index}取名豆{index}",
                quote=f"原话{index}",
                evidence_chat_id=index,
                game="minecraft",
            ),
            lasting=True,
            seen_at_ms=1_000,
        )
        for index in range(1, 11)
    ]
    plain = [_old(index, seen=1_000 + index * 100, game="minecraft") for index in range(11, 19)]

    selection = select_snapshot(_new(6, game="minecraft"), [*names, *plain], session_started_at_ms=_NOW)

    assert all(line.in_snapshot for line in selection.lines)
    kept_ids = [item.archive_line_id for item in selection.kept]
    # 10 条取名全在 + 前几场 8 条普通事件里最近被提起的 6 条（13..18）全在
    assert kept_ids == [*range(1, 11), 13, 14, 15, 16, 17, 18]
    assert [change.what for change in selection.evicted] == ["旧事11", "旧事12"]


def test_world_lasting_quota_reserves_rows_for_name_table_and_evicts_oldest_overflow() -> None:
    """【这个世界里】总展示名额 12 行：7 条取名占 2 行名字表后，其余稳定事实最多留 10 条（最久没提的挤出）。"""
    names = [
        SnapshotLine(
            line=ArchiveLine(
                id=index,
                live_session_id=1,
                text=f"Minecraft：地点{index}取名坡{index}",
                quote=f"原{index}",
                evidence_chat_id=index,
                game="minecraft",
            ),
            lasting=True,
            seen_at_ms=500,
        )
        for index in range(1, 8)
    ]
    other_stable = [_old(index, lasting=True, seen=index * 100, game="minecraft") for index in range(20, 32)]

    selection = select_snapshot(_new(2, game="minecraft"), [*names, *other_stable], session_started_at_ms=_NOW)

    assert [line.in_snapshot for line in selection.lines] == [True, True]
    kept_ids = {item.archive_line_id for item in selection.kept}
    assert set(range(1, 8)).issubset(kept_ids)
    # 12 条非名字稳定里挤掉最老的 2 条（20、21），保留 22..31 共 10 条
    assert kept_ids == {*range(1, 8), *range(22, 32)}
    assert [change.what for change in selection.evicted] == ["旧事20", "旧事21"]
    assert MAX_LASTING_LINES == 12


def test_new_events_beyond_zone_quota_stay_out_of_the_snapshot() -> None:
    """本场游戏普通新事名额为 6 行：模型写了 8 行新事时，前 6 行进快照，后 2 行只落档案行不进快照；
    不挂游戏的直播间记忆独立计额，不被游戏事件挤掉。"""
    room_old = [_old(90, lasting=False, seen=2_000, game=None)]

    selection = select_snapshot(_new(8, game="minecraft"), room_old, session_started_at_ms=_NOW)

    assert [item.archive_line_id for item in selection.kept] == [90]
    assert [line.in_snapshot for line in selection.lines] == [True] * 6 + [False] * 2
    assert [change.what for change in selection.evicted] == ["新事506", "新事507"]


def test_pinned_rows_come_first_and_are_never_squeezed_out() -> None:
    """运营者放回的行排第 0 档：哪怕最久没被再提也不被挤；不占分区名额；放回标记接进新快照。"""
    pinned = [_old(index, seen=10, pinned=True, game="minecraft") for index in (1, 2)]
    stable = [_old(index, lasting=True, seen=index * 100, game="minecraft") for index in range(3, 16)]

    selection = select_snapshot(_new(2, game="minecraft"), [*pinned, *stable], session_started_at_ms=_NOW)

    kept = {item.archive_line_id: item for item in selection.kept}
    assert kept[1].pinned and kept[2].pinned, "放回的行一行不挤，标记照旧带着"
    assert [line.in_snapshot for line in selection.lines] == [True, True]
    # 13 条非名字稳定里留 12 条（4..15），最久没提的 3 被挤出，放回的 1、2 不占这 12 条
    assert sorted(kept) == [1, 2, *range(4, 16)]


def test_more_pinned_rows_than_room_are_all_kept() -> None:
    """放回的行加起来超过总安全上限：全部留着（界面提示人去减），本场新事这次进不了快照。"""
    pinned = [_old(index, pinned=True) for index in range(1, MAX_SNAPSHOT_LINES + 2)]

    selection = select_snapshot(_new(1), pinned, session_started_at_ms=_NOW)

    assert len(selection.kept) == MAX_SNAPSHOT_LINES + 1
    assert [line.in_snapshot for line in selection.lines] == [False]


@pytest.mark.parametrize(
    "op",
    [
        {"old": 1, "op": "drop", "reason": "了结：做完了", "line": 1},
        {"old": 1, "op": "replace", "title": "团子会捡东西", "quote": "团子今天又帮我捡了", "line": 2},
        {"old": 1, "op": "drop", "same_as": 2},
    ],
    ids=["drop", "replace", "same_as"],
)
def test_human_locked_rows_only_humans_can_remove(op: Dict[str, Any]) -> None:
    """人改过的行，模型去掉、替换、按重复去掉都不采纳，理由写进整理报告；旧行照留。"""
    base = [_old(1, human_locked=True), _old(2)]

    plan = _plan([op], base)

    assert [item.line.id for item in plan.kept] == [1, 2] and plan.replacements == []
    assert [(change.action, change.reason) for change in plan.changes] == [("refused", "人改过的行只有人能动")]


def test_human_locked_rows_keep_the_stable_flag_the_human_set() -> None:
    """人标过稳定与否的行，模型重标不算；没锁的照模型重标；保留时放回标记照旧。"""
    base = [_old(1, lasting=True, human_locked=True, pinned=True), _old(2)]

    plan = _plan([{"old": 1, "op": "keep", "lasting": False}, {"old": 2, "op": "keep", "lasting": True}], base)

    assert [(item.lasting, item.pinned) for item in plan.kept] == [(True, True), (True, False)]


def test_duplicate_drop_pointing_at_a_locked_row_does_not_make_it_stable() -> None:
    """按重复去掉一行稳定的事、指向一行人改过且人标了不稳定的行：去掉照采纳，人标的不稳定不变；
    指向没锁的行时照旧把稳定带过去。"""
    base = [_old(1, lasting=True), _old(2, human_locked=True), _old(3, lasting=True), _old(4)]

    plan = _plan([{"old": 1, "op": "drop", "same_as": 2}, {"old": 3, "op": "drop", "same_as": 4}], base)

    assert [(item.line.id, item.lasting, item.line.human_locked) for item in plan.kept] == [
        (2, False, True),
        (4, True, False),
    ]


# ---------------------------------------------------------------------------
# 给模型看的旧行清单
# ---------------------------------------------------------------------------


def test_render_base_marks_previous_and_earlier_rows_with_save_and_stable_flag() -> None:
    """快照所属那一场的行写"上一场"，更早的写"之前"；挂游戏的写上存档，稳定与名场面都标出来。"""
    snapshot = ArchiveSnapshot(
        owner_session_id=2,
        lines=[
            _old(1, session=2, game="minecraft", save_id="abcdef1234", save_name="新的世界", highlight=True),
            _old(2, session=1, lasting=True, game="minecraft", save_id="abcdef1234"),
            _old(3, session=1),
            _old(4, session=1, game="minecraft", save_id="9988776655"),
        ],
    )

    assert render_base(snapshot).splitlines() == [
        "旧1 [上一场] 旧事1（原话「原话1」；在 minecraft「新的世界」；名场面）",
        "旧2 [之前] 旧事2（原话「原话2」；在 minecraft「新的世界」；稳定）",
        "旧3 [之前] 旧事3（原话「原话3」）",
        "旧4 [之前] 旧事4（原话「原话4」；在 minecraft「99887766」）",
    ]
    assert render_base(None) == ""
    assert (MAX_SNAPSHOT_LINES, MAX_BASE_LINES, MAX_HIGHLIGHT_QUOTA, MAX_SESSION_HIGHLIGHTS) == (48, 56, 6, 2)


def test_keep_and_same_as_respect_human_locked_and_unverified_highlight_guards() -> None:
    """未锁定的旧行可用 keep 补标或取消 highlight；human_locked 的旧行无论是 keep 还是 same_as 都不能篡改其
    highlight；未经游戏记录核实的完成说法旧行不能被补标成名场面。"""
    unverified_line = SnapshotLine(
        line=ArchiveLine(
            id=5,
            live_session_id=1,
            text=mark_unverified("Minecraft：修通海底隧道"),
            quote="海底隧道修好啦",
            evidence_chat_id=5,
            game="minecraft",
        ),
        seen_at_ms=1_000,
    )
    base = [
        _old(1, highlight=True, human_locked=True),
        _old(2, highlight=False, human_locked=True),
        _old(3, highlight=False),
        _old(4, highlight=True),
        unverified_line,
        _old(6, highlight=True),
        _old(7, highlight=False),
    ]

    plan = _plan(
        [
            {"old": 1, "op": "keep", "highlight": False},
            {"old": 3, "op": "keep", "highlight": True},
            {"old": 4, "op": "keep", "highlight": False},
            {"old": 5, "op": "keep", "highlight": True},
            {"old": 6, "op": "drop", "same_as": 2},
            {"old": 7, "op": "keep"},
        ],
        base,
    )

    by_id = {item.line.id: item.highlight for item in plan.kept}
    assert by_id == {
        1: True,  # 锁定为名场面：模型取消无效
        2: False,  # 锁定为非名场面：借 same_as 继承也无效
        3: True,  # 未锁定旧行补标名场面生效
        4: False,  # 未锁定旧行取消名场面生效
        5: False,  # 未核实的完成说法强制不标名场面
        7: False,
    }


def test_single_session_caps_new_highlights_at_two_and_rejects_unverified_claims() -> None:
    """单场新事最多保留前 2 条 highlight=True，超出自动降为普通行；未核实完成说法强制降为 highlight=False
    且不白占这 2 个名场面名额。"""
    candidates = [
        PendingArchiveLine(
            text=mark_unverified("Minecraft：末地城拿齐鞘翅"),
            quote="鞘翅拿到啦",
            evidence_chat_id=501,
            game="minecraft",
            highlight=True,
        ),
        *_new(3, highlight=True, start=502, game="minecraft", prefix="名场面"),
    ]

    selection = select_snapshot(candidates, [], session_started_at_ms=_NOW)

    assert [(line.text, line.highlight, line.in_snapshot) for line in selection.lines] == [
        ("Minecraft：她说末地城拿齐鞘翅（游戏记录里没核实到）", False, True),
        ("名场面502", True, True),
        ("名场面503", True, True),
        ("名场面504", False, True),
    ]


def test_highlights_survive_four_subsequent_sessions_with_independent_quota() -> None:
    """名场面拥有按存档独立的 6 条保留名额：跨过 4 场（含薄场与满额普通事件场）仍留在快照里，
    既不被普通事件挤掉，也不挤占【这个世界里】12 行稳定名额和【最近几场】6+6 普通事件名额；
    同一存档超过 6 条名场面时按 pinned 与 seen_at_ms 取舍。"""
    # 第 1 场：立下 1 条名场面 + 12 条稳定事实 + 1 条普通事件
    stable = [_old(idx, session=1, lasting=True, seen=1_000, game="minecraft", save_id="s1") for idx in range(1, 13)]
    first_sel = select_snapshot(
        [
            PendingArchiveLine(
                text="Minecraft：跨海铁路通车翻车",
                quote="第一趟车翻沟里全服爆笑",
                evidence_chat_id=100,
                game="minecraft",
                save_id="s1",
                highlight=True,
            ),
            PendingArchiveLine(
                text="Minecraft：挖了一组煤矿",
                quote="挖了一组煤",
                evidence_chat_id=101,
                game="minecraft",
                save_id="s1",
            ),
        ],
        stable,
        session_started_at_ms=2_000,
    )

    def _to_kept(sel, session_no: int, started_ms: int) -> List[SnapshotLine]:
        by_old = {item.line.id: item.line for item in current_kept}
        rows: List[SnapshotLine] = []
        for idx, pending in enumerate(sel.lines):
            if not pending.in_snapshot:
                continue
            line_id = session_no * 1000 + idx
            rows.append(
                SnapshotLine(
                    line=ArchiveLine(
                        id=line_id,
                        live_session_id=session_no,
                        text=pending.text,
                        quote=pending.quote,
                        evidence_chat_id=pending.evidence_chat_id,
                        game=pending.game,
                        save_id=pending.save_id,
                        highlight=pending.highlight,
                    ),
                    lasting=pending.lasting,
                    seen_at_ms=started_ms,
                    highlight=pending.highlight,
                )
            )
        for ref in sel.kept:
            rows.append(
                SnapshotLine(
                    line=by_old[ref.archive_line_id],
                    lasting=ref.lasting,
                    seen_at_ms=ref.seen_at_ms,
                    pinned=ref.pinned,
                    highlight=ref.highlight,
                )
            )
        return rows

    current_kept = list(stable)
    current_kept = _to_kept(first_sel, 1, 2_000)

    # 连续经过第 2～5 场（2 场薄场 + 2 场各写满 6 条普通事件）：第 1 场的普通挖煤早被挤出，名场面与 12 条稳定全在
    schedules = [
        (2, 3_000, []),
        (3, 4_000, _new(6, start=300, game="minecraft", save_id="s1", prefix="三场琐事")),
        (4, 5_000, []),
        (5, 6_000, _new(6, start=500, game="minecraft", save_id="s1", prefix="五场琐事")),
    ]
    for sess_no, started_ms, session_new in schedules:
        sel = select_snapshot(session_new, current_kept, session_started_at_ms=started_ms)
        current_kept = _to_kept(sel, sess_no, started_ms)

    titles = {item.line.text for item in current_kept}
    assert "Minecraft：跨海铁路通车翻车" in titles
    assert "Minecraft：挖了一组煤矿" not in titles
    assert sum(1 for item in current_kept if item.lasting) == 12
    assert sum(1 for item in current_kept if not item.lasting and not item.highlight) == 12  # 本场 6 + 往前 6

    # 同一存档攒到 7 条名场面（其中最老的一条若标了 pinned 则不挤，改挤次老的未置顶名场面）
    hl_pool = [
        _old(200 + idx, seen=1_000 + idx * 100, pinned=(idx == 0), highlight=True, game="minecraft", save_id="s1")
        for idx in range(7)
    ]
    hl_sel = select_snapshot([], hl_pool, session_started_at_ms=9_000)
    kept_hl_ids = [ref.archive_line_id for ref in hl_sel.kept]
    # 200 因 pinned 留在第 0 档，其余 6 条未置顶名场面（201..206）占满 6 条名额；若再加第 8 条未置顶则挤掉 201
    assert kept_hl_ids == [200, 201, 202, 203, 204, 205, 206]
    hl_pool_8 = [*hl_pool, _old(207, seen=2_000, highlight=True, game="minecraft", save_id="s1")]
    hl_sel_8 = select_snapshot([], hl_pool_8, session_started_at_ms=9_000)
    assert [ref.archive_line_id for ref in hl_sel_8.kept] == [200, 202, 203, 204, 205, 206, 207]
