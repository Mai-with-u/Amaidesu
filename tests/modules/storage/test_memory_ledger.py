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


async def test_list_open_before_session_skips_current_session_and_closed(store: SQLiteDatabase) -> None:
    """了结候选按来源场次排除本场，不再按写入时刻：补整理的旧场承诺写得更晚也要算。"""
    other = await _ended_session(store, started_at_ms=1_000)
    current = await _ended_session(store, started_at_ms=9_000)
    # 上一场的承诺在本场开播之后才写进库（开播时补整理），写入时刻晚于本场开播
    await store.commitments.insert_commitment(what="别的场的约定", created_session_id=other, now_ms_value=20_000)
    await store.commitments.insert_commitment(what="本场登记的", created_session_id=current, now_ms_value=9_600)
    closed = await store.commitments.insert_commitment(what="早就了结的", created_session_id=other, now_ms_value=1_600)
    await store.commitments.insert_commitment(what="运营者手建", now_ms_value=5_000)
    assert closed is not None
    assert await store.commitments.close_commitment(commitment_id=closed)

    rows = await store.commitments.list_open_before_session(live_session_id=current)
    assert [row.what for row in rows] == ["别的场的约定", "运营者手建"]


async def test_list_open_before_session_excludes_later_sessions(store: SQLiteDatabase) -> None:
    """补整理或重新整理较早一场时，更晚场次登记的承诺、本场下播后才手建的条目都不拿来核对。

    否则前一场的台词会把后一场才答应的事判成已了结（时间倒置）。
    """
    earlier = await _ended_session(store, started_at_ms=1_000)
    later = await _ended_session(store, started_at_ms=9_000)
    await store.commitments.insert_commitment(what="更早就答应的", now_ms_value=500)
    await store.commitments.insert_commitment(what="后一场才答应的", created_session_id=later, now_ms_value=9_600)
    await store.commitments.insert_commitment(what="下播后才手建的", now_ms_value=3_000)

    rows = await store.commitments.list_open_before_session(live_session_id=earlier)
    assert [row.what for row in rows] == ["更早就答应的"]

    # 后一场整理时：两条手建条目都早于它下播，都要核对；它自己登记的照旧排除
    later_rows = await store.commitments.list_open_before_session(live_session_id=later)
    assert [row.what for row in later_rows] == ["更早就答应的", "下播后才手建的"]


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
    assert written == {"archive": 1, "commitments": 1, "closed": 1, "claimed": 1}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 9999
    assert len(await store.archive.list_lines(live_session_id=session_id)) == 1
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed" and closed.close_quote == "弄完啦"


async def test_delete_live_session_cascades_archive_lines(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.archive.insert_line(live_session_id=session_id, text="档案", quote="档案", evidence_chat_id=1)
    await store.commitments.insert_commitment(what="承诺不随场次删除", created_session_id=session_id)

    assert await store.sessions.delete_live_session(live_session_id=session_id)
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 1


async def _settle_once(
    store: SQLiteDatabase,
    session_id: int,
    *,
    archive_text: str,
    archive_evidence: int,
    commitment_what: str,
    commitment_evidence: int,
    settled_at_ms: int = 9_999,
) -> dict:
    return await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[PendingArchiveLine(text=archive_text, quote=archive_text, evidence_chat_id=archive_evidence)],
        commitments=[
            PendingCommitment(what=commitment_what, quote=commitment_what, evidence_chat_id=commitment_evidence)
        ],
        closures=[],
        settled_at_ms=settled_at_ms,
        now_ms_value=settled_at_ms,
    )


async def test_apply_settlement_rerun_replaces_archive_and_open_commitments(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    first = await _settle_once(
        store,
        session_id,
        archive_text="旧档案",
        archive_evidence=2,
        commitment_what="旧承诺",
        commitment_evidence=3,
    )
    assert first == {"archive": 1, "commitments": 1, "closed": 0, "claimed": 1}

    closed = await store.commitments.insert_commitment(what="被运营者了结", created_session_id=session_id)
    assert closed is not None
    assert await store.commitments.close_commitment(commitment_id=closed)

    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    second = await _settle_once(
        store,
        session_id,
        archive_text="新档案",
        archive_evidence=5,
        commitment_what="新承诺",
        commitment_evidence=6,
    )
    assert second == {"archive": 1, "commitments": 1, "closed": 0, "claimed": 1}

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [line.text for line in lines] == ["新档案"], "重跑应整场替换档案行，不叠加到超过 8 行"
    whats = [row.what for row in await store.commitments.list_commitments()]
    assert "旧承诺" not in whats, "本场上次登记且仍未了结的承诺应在重跑时被替换"
    assert "新承诺" in whats
    assert "被运营者了结" in whats, "运营者已了结的承诺不随重跑删除"


async def test_rerun_keeps_number_of_commitment_on_the_same_line(store: SQLiteDatabase) -> None:
    """重新整理时，同一句台词这一轮仍认定是承诺：保留原编号、原地更新标题。

    本场台词点名用的是承诺编号（约#N）；重跑换了新编号，旧台词里的引用就指向一条
    已经不存在的承诺，线索推送的"用上即停"也认不出它。这一轮不再认定的照旧删掉，
    判过误记的同一句不会被这一轮复活。
    """
    session_id = await _ended_session(store)
    await _settle_once(
        store,
        session_id,
        archive_text="档案",
        archive_evidence=2,
        commitment_what="接着讲鬼故事",
        commitment_evidence=3,
    )
    (first,) = await store.commitments.list_commitments(statuses=["open"])
    voided = await store.commitments.insert_commitment(
        what="随口说的", evidence_chat_id=4, created_session_id=session_id, now_ms_value=9_999
    )
    assert voided is not None and await store.commitments.void_commitment(commitment_id=voided)

    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    second = await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[],
        commitments=[
            PendingCommitment(what="把鬼故事讲完", quote="下次接着讲", evidence_chat_id=3),
            PendingCommitment(what="随口说的", quote="随口说的", evidence_chat_id=4),
        ],
        closures=[],
        settled_at_ms=10_500,
        now_ms_value=10_500,
    )
    assert second["claimed"] == 1 and second["commitments"] == 1, "只更新了仍未了结的那条，判误记的不算"

    kept = await store.commitments.get_commitment(commitment_id=first.id)
    assert kept is not None and kept.status == "open" and kept.what == "把鬼故事讲完", "同一句保留原编号"
    still_void = await store.commitments.get_commitment(commitment_id=voided)
    assert still_void is not None and still_void.status == "void" and still_void.what == "随口说的"
    assert await store.commitments.count_commitments() == 2

    # 再重跑一轮、不再认定那句是承诺：这条才删掉
    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[],
        commitments=[],
        closures=[],
        settled_at_ms=11_000,
        now_ms_value=11_000,
    )
    assert await store.commitments.get_commitment(commitment_id=first.id) is None


async def _closing_line(store: SQLiteDatabase, session_id: int, content: str) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="assistant",
        content=content,
        message_type="speech",
    )


async def test_rerun_and_rehearsal_reopen_closures_made_by_that_session(store: SQLiteDatabase) -> None:
    """本场台词了结的旧承诺：重新整理时先放回未了结，改成彩排时也放回；运营者手动了结的不动。"""
    session_id = await _ended_session(store, started_at_ms=9_000)
    by_line = await store.commitments.insert_commitment(what="继续炸 TNT", now_ms_value=100)
    by_operator = await store.commitments.insert_commitment(what="运营者了结的", now_ms_value=100)
    assert by_line is not None and by_operator is not None
    assert await store.commitments.close_commitment(commitment_id=by_operator, close_quote="手动了结")
    chat_id = await _closing_line(store, session_id, "TNT 炸完啦")

    first = await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[],
        commitments=[],
        closures=[CommitmentClosure(commitment_id=by_line, close_chat_id=chat_id, close_quote="TNT 炸完啦")],
        settled_at_ms=9_999,
        now_ms_value=9_999,
    )
    assert first["closed"] == 1

    # 重新整理这一轮不再认定那句台词做完了：上一轮的了结不能留在账上
    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    second = await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[],
        commitments=[],
        closures=[],
        settled_at_ms=10_500,
        now_ms_value=10_500,
    )
    assert second["claimed"] == 1
    reopened = await store.commitments.get_commitment(commitment_id=by_line)
    assert reopened is not None and reopened.status == "open" and reopened.close_quote is None
    kept = await store.commitments.get_commitment(commitment_id=by_operator)
    assert kept is not None and kept.status == "closed", "运营者手动了结的不随重跑还原"

    # 再了结一次，然后把这一场改成彩排：彩排台词做出的了结同样作废
    assert await store.sessions.requeue_settlement(live_session_id=session_id)
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[],
        commitments=[],
        closures=[CommitmentClosure(commitment_id=by_line, close_chat_id=chat_id, close_quote="TNT 炸完啦")],
        settled_at_ms=11_000,
        now_ms_value=11_000,
    )
    result = await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="rehearsal", updated_at_ms=12_000
    )
    assert result["reopened"] == 1
    row = await store.commitments.get_commitment(commitment_id=by_line)
    assert row is not None and row.status == "open"
    kept = await store.commitments.get_commitment(commitment_id=by_operator)
    assert kept is not None and kept.status == "closed"


async def test_apply_settlement_refuses_rehearsal_session(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store, kind="rehearsal")
    written = await _settle_once(
        store,
        session_id,
        archive_text="彩排档案",
        archive_evidence=2,
        commitment_what="彩排承诺",
        commitment_evidence=3,
        settled_at_ms=1_500,
    )
    assert written == {"archive": 0, "commitments": 0, "closed": 0, "claimed": 0}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms is None
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 0


async def test_requeue_settlement_rejects_rehearsal_session(store: SQLiteDatabase) -> None:
    rehearsal = await _ended_session(store, kind="rehearsal")
    assert not await store.sessions.requeue_settlement(live_session_id=rehearsal)
    formal = await _ended_session(store, started_at_ms=9_000)
    await store.sessions.mark_settle_done(live_session_id=formal, settled_at_ms=9_500)
    assert await store.sessions.requeue_settlement(live_session_id=formal)


async def test_set_live_session_kind_cleans_ledger_when_switched_to_rehearsal(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.archive.insert_line(
        live_session_id=session_id, text="正式场档案", quote="正式场档案", evidence_chat_id=1
    )
    await store.commitments.insert_commitment(what="正式场承诺", created_session_id=session_id)
    await store.sessions.mark_settle_done(live_session_id=session_id, settled_at_ms=6_000)

    result = await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="rehearsal", updated_at_ms=7_000
    )

    assert result == {"changed": 1, "ledger_removed": 2, "reopened": 0, "requeued": 0}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.kind == "rehearsal" and row.settled_at_ms is None
    assert await store.archive.list_lines(live_session_id=session_id) == []
    assert await store.commitments.count_commitments() == 0


async def test_set_live_session_kind_requeues_migration_marker_when_back_to_live(store: SQLiteDatabase) -> None:
    """v24 迁移把存量场次标成已整理（完成时刻 == 结账时刻）；改回正式场要重新排队。"""
    session_id = await _ended_session(store, kind="rehearsal", started_at_ms=1_000)
    # v24 迁移直接通过 SQL 将所有历史已结账场次刷成 settled_at_ms = ended_at_ms
    await store.execute("UPDATE live_sessions SET settled_at_ms=ended_at_ms WHERE id=?", (session_id,))

    result = await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="live", updated_at_ms=2_000
    )

    assert result == {"changed": 1, "ledger_removed": 0, "reopened": 0, "requeued": 1}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.kind == "live" and row.settled_at_ms is None and row.settle_attempts == 0
    pending = await store.sessions.list_sessions_pending_settle(limit=10)
    assert [item.id for item in pending] == [session_id]


async def test_set_live_session_kind_keeps_real_settlement_and_rejects_ongoing(store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.sessions.mark_settle_done(live_session_id=session_id, settled_at_ms=9_000)
    result = await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=session_id, kind="live", updated_at_ms=9_100
    )
    assert result == {"changed": 1, "ledger_removed": 0, "reopened": 0, "requeued": 0}
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 9_000, "真正整理过的历史场次保持已整理"

    ongoing = await store.sessions.insert_live_session(started_at_ms=20_000)
    result = await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=ongoing, kind="rehearsal", updated_at_ms=20_100
    )
    assert result == {"changed": 0, "ledger_removed": 0, "reopened": 0, "requeued": 0}
