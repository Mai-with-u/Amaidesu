"""ClipRepo 行为：AI 段整批换（不动手建段）、单行增删改、状态回填、按 id 批量取。"""

from __future__ import annotations

from src.modules.storage.models.clipping import ClipSegmentUpdate, NewClipSegment


async def _seed(store, *, count: int = 1) -> list[int]:
    ids = []
    for _ in range(count):
        ids.append(await store.sessions.insert_live_session(started_at_ms=1000))
    return ids


async def test_replace_ai_segments_keeps_manual(store) -> None:
    session_ids = await _seed(store)
    session_id = session_ids[0]

    await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_id, start_ms=1200, end_ms=2200, title="手建段")
    )
    written = await store.clips.replace_ai_segments(
        live_session_id=session_id,
        segments=[
            NewClipSegment(
                live_session_id=session_id,
                origin="ai",
                start_ms=1000,
                end_ms=60000,
                title="提议一",
                reason="开场互动",
            ),
            NewClipSegment(live_session_id=session_id, origin="ai", start_ms=120000, end_ms=180000, title="提议二"),
        ],
    )
    assert written == 2

    segments = await store.clips.list_session_segments(live_session_id=session_id)
    ai_titles = [s.title for s in segments if s.origin == "ai"]
    manual_titles = [s.title for s in segments if s.origin == "manual"]
    assert ai_titles == ["提议一", "提议二"]  # 时间正序
    assert manual_titles == ["手建段"]

    # 重跑选段：旧 AI 段被清掉，手建段仍在
    await store.clips.replace_ai_segments(
        live_session_id=session_id,
        segments=[NewClipSegment(live_session_id=session_id, origin="ai", start_ms=3000, end_ms=63000, title="新提议")],
    )
    segments = await store.clips.list_session_segments(live_session_id=session_id)
    assert [s.title for s in segments if s.origin == "ai"] == ["新提议"]
    assert len([s for s in segments if s.origin == "manual"]) == 1


async def test_update_and_delete_segment(store) -> None:
    session_ids = await _seed(store)
    segment = await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_ids[0], start_ms=1000, end_ms=31000)
    )

    updated = await store.clips.update_segment(
        segment_id=segment.id, update=ClipSegmentUpdate(start_ms=2000, title="改名了", status="confirmed")
    )
    assert updated is not None
    assert (updated.start_ms, updated.title, updated.status) == (2000, "改名了", "confirmed")
    assert updated.end_ms == 31000  # 未指定的字段不动

    assert await store.clips.update_segment(segment_id=99999, update=ClipSegmentUpdate(title="x")) is None
    assert await store.clips.delete_segment(segment_id=segment.id) is True
    assert await store.clips.delete_segment(segment_id=segment.id) is False


async def test_set_status_backfills(store) -> None:
    session_ids = await _seed(store)
    segment = await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_ids[0], start_ms=1000, end_ms=31000)
    )
    assert await store.clips.set_status(segment_id=segment.id, status="done", output_path=r"E:\out\1.mp4")
    done = await store.clips.get_segment(segment_id=segment.id)
    assert done is not None
    assert done.status == "done"
    assert done.output_path == r"E:\out\1.mp4"

    assert await store.clips.set_status(segment_id=99999, status="failed") is False


async def test_list_segments_by_ids_skips_missing_and_orders(store) -> None:
    session_ids = await _seed(store, count=2)
    first = await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_ids[0], start_ms=5000, end_ms=35000, title="B")
    )
    second = await store.clips.add_segment(
        segment=NewClipSegment(live_session_id=session_ids[1], start_ms=1000, end_ms=31000, title="A")
    )
    picked = await store.clips.list_segments_by_ids(segment_ids=[first.id, second.id, 99999])
    assert [s.title for s in picked] == ["A", "B"]  # 按起点排序，缺失 id 静默跳过
    assert await store.clips.list_segments_by_ids(segment_ids=[]) == []
