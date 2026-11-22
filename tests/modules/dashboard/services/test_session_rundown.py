"""流程单执行回看服务：事实行重放的口径断言（goto 回跳 / pause / finish / 进行中）。"""

from __future__ import annotations

from src.modules.dashboard.schemas.session import RundownPlannedSegment
from src.modules.dashboard.services.session_rundown import build_rundown_execution
from src.modules.storage.models.rundown import RundownTransition

BASE = 1_700_000_000_000
END = BASE + 3_600_000


def _t(
    seq: int,
    action: str,
    at: int,
    *,
    seg_id: str = "",
    seg_title: str = "",
    seg_index: int = 0,
    expected_ms: int | None = None,
    rundown_id: str = "rd-1",
    by: str = "agent",
) -> RundownTransition:
    return RundownTransition(
        live_session_id=1,
        rundown_id=rundown_id,
        action=action,
        seg_id=seg_id,
        seg_title=seg_title,
        seg_index=seg_index,
        by=by,
        at_ms=at,
        expected_ms=expected_ms,
        updated_at_ms=at,
    )


def test_walk_switch_pause_finish() -> None:
    transitions = [
        _t(1, "load", BASE + 0, seg_id="s1", seg_title="开场", seg_index=0, expected_ms=600_000),
        _t(2, "pause", BASE + 300_000, seg_id="s1", seg_title="开场", seg_index=0, expected_ms=600_000),
        _t(3, "resume", BASE + 420_000, seg_id="s1", seg_title="开场", seg_index=0, expected_ms=600_000),
        _t(4, "next", BASE + 600_000, seg_id="s2", seg_title="正题", seg_index=1, expected_ms=1_800_000),
        _t(5, "finish", END, seg_index=2),
    ]
    body = build_rundown_execution(1, transitions, ended_at_ms=END)

    assert body.rundown_id == "rd-1"
    assert body.transitions_count == len(transitions)
    assert [(s.seg_id, s.entered_at_ms, s.left_at_ms) for s in body.segments] == [
        ("s1", BASE + 0, BASE + 600_000),
        ("s2", BASE + 600_000, END),
    ]
    # s1 墙钟 600s，其中暂停 120s 单列；s2 到 finish 封口 3000s
    assert (body.segments[0].dwell_ms, body.segments[0].pause_ms) == (600_000, 120_000)
    assert (body.segments[1].dwell_ms, body.segments[1].pause_ms) == (3_000_000, 0)
    assert (body.segments[0].expected_ms, body.segments[1].expected_ms) == (600_000, 1_800_000)
    assert body.pause_total_ms == 120_000


def test_goto_back_to_same_segment_reopens() -> None:
    transitions = [
        _t(1, "load", BASE + 0, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(2, "goto", BASE + 100_000, seg_id="s2", seg_index=1, expected_ms=600_000),
        _t(3, "goto", BASE + 200_000, seg_id="s1", seg_index=0, expected_ms=600_000),
    ]
    body = build_rundown_execution(1, transitions, ended_at_ms=BASE + 300_000)

    # 回跳同一段 = 新的一行（两次进入各算各的停留）
    assert [s.seg_id for s in body.segments] == ["s1", "s2", "s1"]
    assert [s.dwell_ms for s in body.segments] == [100_000, 100_000, 100_000]


def test_same_segment_redeclaration_and_reset_keeps_open() -> None:
    transitions = [
        _t(1, "load", BASE + 0, seg_id="s1", seg_index=0, expected_ms=600_000),
        # renew/restore 重复声明同段：不重开
        _t(2, "renew", BASE + 50_000, seg_id="s1", seg_index=0, expected_ms=600_000),
        # 游标重置（编辑界面直写，index=-1 无环节）：关闭当前段
        _t(3, "restore", BASE + 100_000, seg_index=-1),
        _t(4, "load", BASE + 200_000, seg_id="s2", seg_index=0, expected_ms=900_000),
    ]
    body = build_rundown_execution(1, transitions, ended_at_ms=BASE + 400_000)

    assert [(s.seg_id, s.dwell_ms) for s in body.segments] == [("s1", 100_000), ("s2", 200_000)]


def test_ongoing_session_tail_left_open() -> None:
    transitions = [
        _t(1, "load", BASE + 0, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(2, "next", BASE + 300_000, seg_id="s2", seg_index=1, expected_ms=1_800_000),
        _t(3, "pause", BASE + 400_000, seg_id="s2", seg_index=1, expected_ms=1_800_000),
    ]
    # 结束边界未知（进行中场次）：尾段不封口、停留不计，暂停也不结转
    body = build_rundown_execution(1, transitions, ended_at_ms=None)

    assert [(s.seg_id, s.left_at_ms, s.dwell_ms) for s in body.segments] == [
        ("s1", BASE + 300_000, 300_000),
        ("s2", None, 0),
    ]
    assert body.segments[1].pause_ms == 0 and body.pause_total_ms == 0


def test_empty_transitions() -> None:
    body = build_rundown_execution(7, [], ended_at_ms=END)
    assert (body.live_session_id, body.segments, body.transitions_count) == (7, [], 0)


def test_segment_run_carries_who_switched_and_pause_count() -> None:
    """每段带"谁切的/什么动作进的"，暂停次数单列。"""
    transitions = [
        _t(1, "load", BASE + 0, seg_id="s1", seg_title="开场", seg_index=0, expected_ms=600_000, by="system"),
        _t(2, "pause", BASE + 100_000, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(3, "resume", BASE + 160_000, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(4, "pause", BASE + 200_000, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(5, "resume", BASE + 240_000, seg_id="s1", seg_index=0, expected_ms=600_000),
        _t(6, "next", BASE + 300_000, seg_id="s2", seg_title="正题", seg_index=1, expected_ms=1_800_000, by="human"),
    ]
    body = build_rundown_execution(1, transitions, ended_at_ms=BASE + 400_000)

    assert [(s.by, s.action) for s in body.segments] == [("system", "load"), ("human", "next")]
    assert body.segments[0].pause_count == 2
    assert body.segments[1].pause_count == 0


def test_planned_segments_pass_through() -> None:
    """计划环节全量（含未到达的）原样带回，与执行视图并列。"""
    planned = [
        RundownPlannedSegment(seq=0, seg_id="s1", title="开场", expected_ms=600_000),
        RundownPlannedSegment(seq=1, seg_id="s2", title="正题", expected_ms=1_800_000),
    ]
    body = build_rundown_execution(
        1,
        [_t(1, "load", BASE + 0, seg_id="s1", seg_index=0, expected_ms=600_000)],
        ended_at_ms=None,
        planned_segments=planned,
    )

    assert [(p.seq, p.seg_id, p.title) for p in body.planned_segments] == [
        (0, "s1", "开场"),
        (1, "s2", "正题"),
    ]


def test_planned_segments_default_empty() -> None:
    body = build_rundown_execution(1, [], ended_at_ms=END)
    assert body.planned_segments == []
