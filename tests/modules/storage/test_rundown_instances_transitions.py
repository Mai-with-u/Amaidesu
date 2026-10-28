"""流程单实例快照与变更事实仓储：instances upsert / 全量替换幂等 / get 组装，transitions insert / list"""

from __future__ import annotations

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.rundown import RundownInstance, RundownInstanceSegment, RundownTransition


def _snapshot(live_session_id: int = 1, *, seg_index: int = 1, paused: bool = False) -> RundownInstance:
    return RundownInstance(
        live_session_id=live_session_id,
        template_id="rd_tpl",
        seg_index=seg_index,
        segment_started_at_ms=1_000,
        paused_at_ms=5_000 if paused else None,
        rundown_started_at_ms=900,
        accumulated_pause_ms=120,
        accumulated_session_pause_ms=340,
        updated_at_ms=9_999,
        segments=[
            RundownInstanceSegment(
                seq=0,
                seg_id="opening",
                title="开场",
                task_description="打招呼",
                expected_ms=60_000,
                min_duration_ms=30_000,
                notes="备注",
            ),
            RundownInstanceSegment(
                seq=1,
                seg_id="chat",
                title="闲聊",
                task_description="互动",
                expected_ms=300_000,
                min_duration_ms=None,
                notes=None,
            ),
        ],
    )


async def test_instance_get_returns_none_when_absent(store: SQLiteDatabase) -> None:
    assert await store.rundown_instances.get_by_session(42) is None


async def test_instance_upsert_and_get_roundtrip(store: SQLiteDatabase) -> None:
    snapshot = _snapshot(1, paused=True)
    await store.rundown_instances.upsert(snapshot)

    fetched = await store.rundown_instances.get_by_session(1)
    assert fetched == snapshot.model_copy(update={"updated_at_ms": fetched.updated_at_ms})
    assert (fetched.paused_at_ms, fetched.seg_index) == (5_000, 1)
    assert [(s.seq, s.seg_id, s.min_duration_ms, s.notes) for s in fetched.segments] == [
        (0, "opening", 30_000, "备注"),
        (1, "chat", None, None),
    ]


async def test_instance_upsert_replaces_segment_rows_atomically(store: SQLiteDatabase) -> None:
    """环节行事务内全量替换：追加环节后旧行不残留（快照语义，防模板漂移）。"""
    await store.rundown_instances.upsert(_snapshot(1))
    snapshot = _snapshot(1)
    snapshot.segments.append(
        RundownInstanceSegment(seq=2, seg_id="extra", title="追加环节", task_description="续命", expected_ms=60_000)
    )
    snapshot.seg_index = 2
    snapshot.paused_at_ms = None
    await store.rundown_instances.upsert(snapshot)

    rows = await store.execute("SELECT seq, seg_id FROM rundown_instance_segments WHERE live_session_id=1 ORDER BY seq")
    assert [(r["seq"], r["seg_id"]) for r in rows] == [(0, "opening"), (1, "chat"), (2, "extra")]
    fetched = await store.rundown_instances.get_by_session(1)
    assert fetched.seg_index == 2 and fetched.paused_at_ms is None


async def test_instance_delete_by_session(store: SQLiteDatabase) -> None:
    await store.rundown_instances.upsert(_snapshot(7))
    assert await store.rundown_instances.delete_by_session(7) is True
    assert await store.rundown_instances.delete_by_session(7) is False
    rows = await store.execute("SELECT COUNT(*) AS n FROM rundown_instance_segments WHERE live_session_id=7")
    assert rows[0]["n"] == 0


async def test_transition_insert_and_list_by_session(store: SQLiteDatabase) -> None:
    for session_id, action, at_ms in ((1, "load", 100), (1, "next", 200), (2, "load", 150)):
        await store.rundown_transitions.insert(
            RundownTransition(
                live_session_id=session_id,
                rundown_id="rd_tpl",
                action=action,
                seg_id="opening" if action == "load" else "chat",
                seg_title="开场" if action == "load" else "闲聊",
                seg_index=0 if action == "load" else 1,
                by="agent",
                at_ms=at_ms,
                expected_ms=60_000,
                task_description="打招呼" if action == "load" else None,
                updated_at_ms=0,
            )
        )

    rows = await store.rundown_transitions.list_by_session(1)
    assert [(r.action, r.at_ms) for r in rows] == [("load", 100), ("next", 200)]
    assert rows[0].task_description == "打招呼" and rows[1].task_description is None
    assert [r.action for r in await store.rundown_transitions.list_by_session(2)] == ["load"]
    assert await store.rundown_transitions.list_by_session(3) == []


async def test_transition_list_with_limit_returns_most_recent_in_ascending_order(store: SQLiteDatabase) -> None:
    for i in range(5):
        await store.rundown_transitions.insert(
            RundownTransition(
                live_session_id=1,
                rundown_id="rd",
                action=f"act{i}",
                seg_index=0,
                by="agent",
                at_ms=i * 100,
                updated_at_ms=0,
            )
        )
    rows = await store.rundown_transitions.list_by_session(1, limit=2)
    assert [r.action for r in rows] == ["act3", "act4"]  # 最近 2 条，返回仍按时间升序
