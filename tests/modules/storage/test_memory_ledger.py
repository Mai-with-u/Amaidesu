"""承诺账本与场次档案仓储、场次整理进度的读写。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import (
    CommitmentClosure,
    PendingArchiveLine,
    PendingCommitment,
)


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "ledger.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _ended_session(store: SQLiteDatabase, *, kind: str = "live", started_at_ms: int = 1000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 500)
    return session_id


# ---------------------------------------------------------------------------
# 承诺
# ---------------------------------------------------------------------------


async def test_insert_and_close_commitment_only_touches_open(store: SQLiteDatabase) -> None:
    commitment_id = await store.commitments.insert_commitment(
        what="接着讲鬼故事的结局", quote="下次接着讲那个鬼故事的结局", evidence_chat_id=11
    )
    assert commitment_id is not None

    assert await store.commitments.close_commitment(commitment_id=commitment_id, close_quote="讲完啦")
    row = await store.commitments.get_commitment(commitment_id=commitment_id)
    assert row is not None
    assert row.status == "closed"
    assert row.close_quote == "讲完啦"

    # 已了结的不再被改：她再说一遍也翻不回 open 之外的另一个结论
    assert not await store.commitments.close_commitment(commitment_id=commitment_id, close_quote="又说一遍")


async def test_insert_commitment_dedupes_same_evidence_line(store: SQLiteDatabase) -> None:
    first = await store.commitments.insert_commitment(what="A", evidence_chat_id=7)
    second = await store.commitments.insert_commitment(what="B", evidence_chat_id=7)
    assert first is not None
    assert second is None
    assert await store.commitments.count_commitments() == 1


async def test_operator_created_commitments_may_share_empty_evidence(store: SQLiteDatabase) -> None:
    # 手建条目没有原话行，多条 NULL 在唯一索引下互不冲突
    assert await store.commitments.insert_commitment(what="手建一") is not None
    assert await store.commitments.insert_commitment(what="手建二") is not None
    assert await store.commitments.count_commitments() == 2


async def test_void_keeps_tombstone_and_counts_by_status(store: SQLiteDatabase) -> None:
    commitment_id = await store.commitments.insert_commitment(what="明天继续炸 TNT", evidence_chat_id=3)
    assert commitment_id is not None
    assert await store.commitments.void_commitment(commitment_id=commitment_id)
    row = await store.commitments.get_commitment(commitment_id=commitment_id)
    assert row is not None and row.status == "void"
    assert await store.commitments.count_commitments(statuses=["open"]) == 0
    assert await store.commitments.count_commitments(statuses=["void"]) == 1
    assert await store.commitments.count_commitments() == 1

    listed = await store.commitments.list_commitments(statuses=["void"])
    assert [item.id for item in listed] == [commitment_id]


async def test_list_open_before_skips_newer_and_closed(store: SQLiteDatabase) -> None:
    old = await store.commitments.insert_commitment(what="上上场的约定", now_ms_value=1000)
    await store.commitments.insert_commitment(what="本场的约定", now_ms_value=5000)
    closed = await store.commitments.insert_commitment(what="早就了结的", now_ms_value=900)
    assert old is not None and closed is not None
    assert await store.commitments.close_commitment(commitment_id=closed)

    rows = await store.commitments.list_open_before(before_ms=3000)
    assert [row.what for row in rows] == ["上上场的约定"]


async def test_delete_by_session_removes_registered_commitments(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    kept = await store.commitments.insert_commitment(what="别的场次")
    assert kept is not None
    await store.commitments.insert_commitment(what="本场的", created_session_id=session_id)
    assert await store.commitments.delete_by_session(live_session_id=session_id) == 1
    rows = await store.commitments.list_commitments()
    assert [row.what for row in rows] == ["别的场次"]


# ---------------------------------------------------------------------------
# 场次档案
# ---------------------------------------------------------------------------


async def test_archive_lines_roundtrip_and_delete(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    first = await store.archive.insert_line(
        live_session_id=session_id, text="Minecraft：水车搭好了", quote="水车搭好啦", evidence_chat_id=5
    )
    assert first > 0
    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["Minecraft：水车搭好了"]
    assert await store.archive.get_last_archived_session_id() == session_id

    assert await store.archive.delete_line(line_id=first)
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.archive.get_last_archived_session_id() is None


async def test_last_archived_session_ignores_rehearsal_and_empty(store: SQLiteDatabase) -> None:
    formal = await _ended_session(store, started_at_ms=1000)
    await store.archive.insert_line(live_session_id=formal, text="正式场", quote="正式场", evidence_chat_id=1)
    rehearsal = await _ended_session(store, kind="rehearsal", started_at_ms=9000)
    await store.archive.insert_line(live_session_id=rehearsal, text="彩排", quote="彩排", evidence_chat_id=2)
    assert await store.archive.get_last_archived_session_id() == formal
    assert await store.archive.get_last_archived_session_id(kind="rehearsal") == rehearsal


# ---------------------------------------------------------------------------
# 整理进度
# ---------------------------------------------------------------------------


async def test_pending_settle_only_formal_ended_and_under_retry_limit(store: SQLiteDatabase) -> None:
    target = await _ended_session(store, started_at_ms=2000)
    await _ended_session(store, kind="rehearsal", started_at_ms=1500)
    ongoing = await store.sessions.insert_live_session(started_at_ms=3000)
    assert ongoing > 0
    exhausted = await _ended_session(store, started_at_ms=1000)
    for _ in range(3):
        await store.sessions.record_settle_failure(live_session_id=exhausted, settle_report="{}")

    pending = await store.sessions.list_sessions_pending_settle(limit=10, max_attempts=3)
    assert [row.id for row in pending] == [target]


async def test_settle_failure_counter_and_reset(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    assert await store.sessions.record_settle_failure(live_session_id=session_id, settle_report='{"a":1}') == 1
    assert await store.sessions.record_settle_failure(live_session_id=session_id, settle_report='{"a":2}') == 2
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_attempts == 2 and row.settle_report == '{"a":2}'

    assert await store.sessions.reset_settle_attempts(live_session_id=session_id)
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settle_attempts == 0 and row.settle_report is None


async def test_apply_settlement_writes_everything_in_one_call(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    old = await store.commitments.insert_commitment(what="旧的", evidence_chat_id=1)
    assert old is not None

    written = await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[PendingArchiveLine(text="档案", quote="档案", evidence_chat_id=2)],
        commitments=[PendingCommitment(what="新承诺", quote="下次弄", evidence_chat_id=3, game="Minecraft")],
        closures=[CommitmentClosure(commitment_id=old, close_chat_id=4, close_quote="弄完啦")],
        settled_at_ms=9999,
        now_ms_value=9999,
    )
    assert written == {"archive": 1, "commitments": 1, "closed": 1}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 9999
    assert len(await store.archive.list_lines(live_session_id=session_id)) == 1
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed" and closed.close_quote == "弄完啦"


async def test_reset_memory_ledger_clears_session_traces(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.archive.insert_line(
        live_session_id=session_id, text="彩排前写的", quote="彩排前写的", evidence_chat_id=1
    )
    await store.commitments.insert_commitment(what="本场登记", created_session_id=session_id)
    await store.sessions.mark_settle_done(live_session_id=session_id, settled_at_ms=5000)

    removed = await store.sessions.reset_memory_ledger_for_session(live_session_id=session_id)
    assert removed == 2
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms is None
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 0


async def test_delete_live_session_cascades_archive_lines(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.archive.insert_line(live_session_id=session_id, text="档案", quote="档案", evidence_chat_id=1)
    await store.commitments.insert_commitment(what="承诺不随场次删除", created_session_id=session_id)

    assert await store.sessions.delete_live_session(live_session_id=session_id)
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 1
