"""事件历史游标与按场过滤查询测试（get_since / get_by_session）。"""

from __future__ import annotations

from src.modules.events.event_history import EventHistoryService, EventRecord


def _record(n: int, *, session: int | None = None) -> EventRecord:
    return EventRecord(
        id=f"evt-{n:03d}",
        type="planner.decision" if n % 2 == 0 else "streamer.stage",
        level="info",
        source=str(session or ""),
        summary=f"事件{n}",
        data={"live_session_id": session, "n": n} if session is not None else {"n": n},
        timestamp=1_700_000_000.0 + n,
    )


def test_get_since_returns_gap_after_cursor() -> None:
    svc = EventHistoryService(max_events=100, persist=False)
    for i in range(10):
        svc.record(_record(i))
    gap = svc.get_since("evt-005", limit=100)
    assert [r.id for r in gap] == [f"evt-{i:03d}" for i in range(6, 10)]


def test_get_since_unknown_cursor_returns_recent_window() -> None:
    svc = EventHistoryService(max_events=100, persist=False)
    for i in range(10):
        svc.record(_record(i))
    gap = svc.get_since("evt-not-exist", limit=4)
    assert [r.id for r in gap] == [f"evt-{i:03d}" for i in range(6, 10)]


def test_get_by_session_filters_and_orders() -> None:
    svc = EventHistoryService(max_events=100, persist=False)
    for i in range(10):
        svc.record(_record(i, session=7 if i % 3 == 0 else 8))
    hits = svc.get_by_session(7, limit=100)
    assert [r.data["n"] for r in hits] == [0, 3, 6, 9]
    assert all(r.data["live_session_id"] == 7 for r in hits)


def test_ring_buffer_eviction_keeps_get_since_safe() -> None:
    svc = EventHistoryService(max_events=5, persist=False)
    for i in range(20):
        svc.record(_record(i))
    # 游标指向已被淘汰的早期事件 → 退化为最近窗口（5 条）
    gap = svc.get_since("evt-002", limit=100)
    assert len(gap) == 5
    assert gap[0].id == "evt-015"
