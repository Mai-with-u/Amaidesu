"""承诺账本与场次档案仓储、场次整理进度的读写。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import (
    ArchiveSnapshot,
    CommitmentClosure,
    KeptSnapshotLine,
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


async def _ended_session(
    store: SQLiteDatabase, *, kind: str = "live", started_at_ms: int = 1000, duration_ms: int = 500
) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + duration_ms)
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


async def _settle_with_snapshot(
    store: SQLiteDatabase,
    session_id: int,
    *,
    new: list[PendingArchiveLine],
    kept: list[KeptSnapshotLine] | None = None,
    settled_at_ms: int = 9_999,
) -> dict:
    return await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=new,
        commitments=[],
        closures=[],
        settled_at_ms=settled_at_ms,
        now_ms_value=settled_at_ms,
        snapshot_kept=kept or [],
    )


def _snapshot_rows(snapshot: ArchiveSnapshot | None) -> list[tuple]:
    assert snapshot is not None
    return [
        (item.line.id, item.line.live_session_id, item.line.text, item.lasting, item.seen_at_ms)
        for item in snapshot.lines
    ]


async def test_snapshot_references_kept_rows_without_copying(store: SQLiteDatabase) -> None:
    """第二场接住第一场的一行：快照引用第一场那一行本身，第二场名下只有它自己新写的行；
    被挤出快照的新行照样写成本场档案行。"""
    first = await _ended_session(store, started_at_ms=1_000)
    await _settle_with_snapshot(
        store,
        first,
        new=[
            PendingArchiveLine(text="给狼取名", quote="就叫阿黄", evidence_chat_id=1, lasting=True),
            PendingArchiveLine(text="挖到铁矿", quote="铁矿来了", evidence_chat_id=2),
        ],
    )
    (named, mined) = await store.archive.list_lines(live_session_id=first)
    second = await _ended_session(store, started_at_ms=2_000)

    written = await _settle_with_snapshot(
        store,
        second,
        new=[
            PendingArchiveLine(text="盖好木屋", quote="木屋盖好了", evidence_chat_id=3),
            PendingArchiveLine(text="挤出去的新事", quote="挤出去", evidence_chat_id=4, in_snapshot=False),
        ],
        kept=[KeptSnapshotLine(archive_line_id=named.id, lasting=True, seen_at_ms=1_000)],
    )

    own = await store.archive.list_lines(live_session_id=second)
    assert [line.text for line in own] == ["盖好木屋", "挤出去的新事"]
    assert written["snapshot"] == 2
    assert _snapshot_rows(await store.archive.get_snapshot(live_session_id=second)) == [
        (own[0].id, second, "盖好木屋", False, 2_000),
        (named.id, first, "给狼取名", True, 1_000),
    ]
    assert _snapshot_rows(await store.archive.get_snapshot(live_session_id=first)) == [
        (named.id, first, "给狼取名", True, 1_000),
        (mined.id, first, "挖到铁矿", False, 1_000),
    ]


async def test_deleting_a_row_removes_it_from_every_snapshot(store: SQLiteDatabase) -> None:
    """删单行、按句删台词、改彩排、删场次：被删的档案行从所有快照里撤掉，别的行不动。"""
    first = await _ended_session(store, started_at_ms=1_000)
    await _settle_with_snapshot(
        store,
        first,
        new=[
            PendingArchiveLine(text="甲", quote="甲", evidence_chat_id=1),
            PendingArchiveLine(text="乙", quote="乙", evidence_chat_id=2),
        ],
    )
    (line_a, line_b) = await store.archive.list_lines(live_session_id=first)
    second = await _ended_session(store, started_at_ms=2_000)
    await _settle_with_snapshot(
        store,
        second,
        new=[PendingArchiveLine(text="丙", quote="丙", evidence_chat_id=3)],
        kept=[
            KeptSnapshotLine(archive_line_id=line_a.id, seen_at_ms=1_000),
            KeptSnapshotLine(archive_line_id=line_b.id, seen_at_ms=1_000),
        ],
    )

    assert await store.archive.delete_line(line_id=line_a.id)
    snapshot = await store.archive.get_snapshot(live_session_id=second)
    assert [item.line.text for item in snapshot.lines] == ["丙", "乙"]

    await store.sessions.set_live_session_kind_with_ledger(live_session_id=first, kind="rehearsal", updated_at_ms=1)
    snapshot = await store.archive.get_snapshot(live_session_id=second)
    assert [item.line.text for item in snapshot.lines] == ["丙"]
    assert await store.archive.get_snapshot(live_session_id=first) is None

    assert await store.sessions.delete_live_session(live_session_id=second)
    assert await store.archive.get_snapshot(live_session_id=second) is None


async def test_resettle_replaces_own_snapshot_and_skips_rows_deleted_meanwhile(store: SQLiteDatabase) -> None:
    """重新整理一场：本场旧快照整份替换（此前的去掉随之作废）；整理途中被删的行不再引用；空快照也算写过。"""
    first = await _ended_session(store, started_at_ms=1_000)
    await _settle_with_snapshot(store, first, new=[PendingArchiveLine(text="甲", quote="甲", evidence_chat_id=1)])
    (line_a,) = await store.archive.list_lines(live_session_id=first)
    second = await _ended_session(store, started_at_ms=2_000)
    await _settle_with_snapshot(store, second, new=[])
    empty = await store.archive.get_snapshot(live_session_id=second)
    assert empty is not None and empty.lines == []

    assert await store.sessions.requeue_settlement(live_session_id=second)
    await _settle_with_snapshot(
        store,
        second,
        new=[PendingArchiveLine(text="乙", quote="乙", evidence_chat_id=2)],
        kept=[
            KeptSnapshotLine(archive_line_id=line_a.id, seen_at_ms=1_000),
            KeptSnapshotLine(archive_line_id=987_654, seen_at_ms=1_000),
        ],
    )

    snapshot = await store.archive.get_snapshot(live_session_id=second)
    assert [item.line.text for item in snapshot.lines] == ["乙", "甲"]


async def test_snapshot_before_seeds_from_old_archives_and_stops_at_a_stored_one(store: SQLiteDatabase) -> None:
    """滚动之前的旧场次现拼快照：它自己的行加往前几场的档案行，凑够两场时长够的就停（开错重开的一小段
    不算），遇到存下的快照接上它就停；同一句原话只留最新的一行；彩排与开播晚于本场的不算。"""
    stored = await _ended_session(store, started_at_ms=1_000, duration_ms=3_600_000)
    await _settle_with_snapshot(store, stored, new=[PendingArchiveLine(text="存下的", quote="存", evidence_chat_id=1)])
    old_a = await _ended_session(store, started_at_ms=4_000_000, duration_ms=3_600_000)
    await store.archive.insert_line(live_session_id=old_a, text="召出女仆取名", quote="就叫团子", evidence_chat_id=2)
    rehearsal = await _ended_session(store, kind="rehearsal", started_at_ms=8_000_000)
    await store.archive.insert_line(live_session_id=rehearsal, text="彩排", quote="彩排", evidence_chat_id=3)
    false_start = await _ended_session(store, started_at_ms=9_000_000, duration_ms=60_000)
    await store.archive.insert_line(live_session_id=false_start, text="同一句原话", quote="团子", evidence_chat_id=2)
    old_b = await _ended_session(store, started_at_ms=10_000_000, duration_ms=3_600_000)
    await store.archive.insert_line(live_session_id=old_b, text="菜地围好了", quote="围好啦", evidence_chat_id=4)
    later = await _ended_session(store, started_at_ms=20_000_000)
    await store.archive.insert_line(live_session_id=later, text="之后的场次", quote="之后", evidence_chat_id=5)

    seeded = await store.archive.snapshot_before(before_started_at_ms=15_000_000, min_duration_ms=300_000)
    assert seeded is not None and seeded.owner_session_id == old_b and not seeded.stored
    assert [(item.line.text, item.line.live_session_id, item.seen_at_ms) for item in seeded.lines] == [
        ("菜地围好了", old_b, 10_000_000),
        ("同一句原话", false_start, 9_000_000),
    ], "old_b 与 old_a 凑够两场时长够的就停，其间开错重开的一小段照取；old_a 那行与它同一句原话只留新的"

    reach_stored = await store.archive.snapshot_before(before_started_at_ms=5_000_000, min_duration_ms=300_000)
    assert [item.line.text for item in reach_stored.lines] == ["召出女仆取名", "存下的"]
    first_stored = await store.archive.snapshot_before(before_started_at_ms=2_000)
    assert first_stored.stored and [item.line.text for item in first_stored.lines] == ["存下的"]
    assert await store.archive.snapshot_before(before_started_at_ms=500) is None


async def test_latest_snapshot_is_the_previous_long_enough_session(store: SQLiteDatabase) -> None:
    """简报读的快照：最近一个时长够的场次（写过快照的空场次也算）；开错重开的一小段跳过。"""
    real = await _ended_session(store, started_at_ms=1_000, duration_ms=3_600_000)
    await _settle_with_snapshot(store, real, new=[])
    false_start = await _ended_session(store, started_at_ms=4_000_000, duration_ms=60_000)
    await store.archive.insert_line(live_session_id=false_start, text="开错的", quote="开错", evidence_chat_id=1)

    snapshot = await store.archive.latest_snapshot(min_duration_ms=300_000)
    assert snapshot is not None and snapshot.owner_session_id == real and snapshot.lines == []
    assert await store.archive.get_last_archived_session_id(min_duration_ms=300_000) == real
    assert (await store.archive.latest_snapshot()).owner_session_id == false_start


async def test_last_archived_session_ignores_rehearsal_and_empty(store: SQLiteDatabase) -> None:
    formal = await _ended_session(store, started_at_ms=1000)
    await store.archive.insert_line(live_session_id=formal, text="正式场", quote="正式场", evidence_chat_id=1)
    rehearsal = await _ended_session(store, kind="rehearsal", started_at_ms=9000)
    await store.archive.insert_line(live_session_id=rehearsal, text="彩排", quote="彩排", evidence_chat_id=2)
    assert await store.archive.get_last_archived_session_id() == formal
    assert await store.archive.get_last_archived_session_id(kind="rehearsal") == rehearsal


async def test_last_archived_session_skips_sessions_shorter_than_minimum(store: SQLiteDatabase) -> None:
    """开错后马上重开的那一小段（1.5 分钟）也有档案行，设了最短时长就不算上一场，往前找到正经的那一场；
    还没结束的场次量不出时长，按够长处理。"""
    real = await _ended_session(store, started_at_ms=1_000, duration_ms=49 * 60_000)
    await store.archive.insert_line(live_session_id=real, text="钻石剑做好了", quote="钻石剑到手", evidence_chat_id=1)
    false_start = await _ended_session(store, started_at_ms=4_000_000, duration_ms=90_000)
    await store.archive.insert_line(
        live_session_id=false_start, text="下次接着救", quote="下次接着救", evidence_chat_id=2
    )

    assert await store.archive.get_last_archived_session_id() == false_start
    assert await store.archive.get_last_archived_session_id(min_duration_ms=300_000) == real

    unfinished = await store.sessions.insert_live_session(started_at_ms=9_000_000)
    await store.archive.insert_line(live_session_id=unfinished, text="没收尾的一场", quote="先这样", evidence_chat_id=3)
    assert await store.archive.get_last_archived_session_id(min_duration_ms=300_000) == unfinished


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
    assert written == {
        "archive": 1,
        "snapshot": 1,
        "commitments": 1,
        "closed": 1,
        "claimed": 1,
        "candidates": 0,
        "player_candidates": 0,
        "social_facts": 0,
        "interactions": 0,
    }
    row = await store.sessions.get_live_session(live_session_id=session_id)
    assert row is not None and row.settled_at_ms == 9999
    assert len(await store.archive.list_lines(live_session_id=session_id)) == 1
    closed = await store.commitments.get_commitment(commitment_id=old)
    assert closed is not None and closed.status == "closed" and closed.close_quote == "弄完啦"


async def test_apply_settlement_keeps_game_and_save_of_lines(store: SQLiteDatabase) -> None:
    """整理写下的档案行与承诺带着游戏与存档；与游戏世界无关的行这几列为空。"""
    session_id = await _ended_session(store)
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[
            PendingArchiveLine(
                text="Minecraft：屋子盖好了",
                quote="屋子盖好啦",
                evidence_chat_id=2,
                game="minecraft",
                save_id="ab12",
                save_name="新的世界",
            ),
            PendingArchiveLine(text="和大家定了猫的名字", quote="就叫团子", evidence_chat_id=3),
        ],
        commitments=[
            PendingCommitment(
                what="把屋顶封上",
                quote="下次把屋顶封上",
                evidence_chat_id=4,
                game="minecraft",
                save_id="ab12",
                save_name="新的世界",
            )
        ],
        closures=[],
        settled_at_ms=9999,
        now_ms_value=9999,
    )

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [(line.game, line.save_id, line.save_name) for line in lines] == [
        ("minecraft", "ab12", "新的世界"),
        (None, "", ""),
    ]
    [commitment] = await store.commitments.list_commitments(statuses=["open"])
    assert (commitment.game, commitment.save_id, commitment.save_name) == ("minecraft", "ab12", "新的世界")
    # 运营者手建的承诺不带存档
    manual = await store.commitments.insert_commitment(what="手建", game="minecraft")
    assert manual is not None
    row = await store.commitments.get_commitment(commitment_id=manual)
    assert row is not None and (row.save_id, row.save_name) == ("", "")


async def test_archive_lines_keep_keywords_and_place_and_read_bad_keywords_as_none(store: SQLiteDatabase) -> None:
    """整理写下的触发词与地点原样读回（快照里也一样）；触发词列坏了的当没有触发词，不拖垮读档案。"""
    session_id = await _ended_session(store)
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=[
            PendingArchiveLine(
                text="Minecraft：灯塔盖好了",
                quote="灯塔盖好啦",
                evidence_chat_id=2,
                game="minecraft",
                keywords=["灯塔", "小百合"],
                place="海边营地",
            ),
            PendingArchiveLine(text="和大家定了猫的名字", quote="就叫团子", evidence_chat_id=3),
        ],
        commitments=[],
        closures=[],
        settled_at_ms=9999,
        now_ms_value=9999,
    )

    lines = await store.archive.list_lines(live_session_id=session_id)
    assert [(line.keywords, line.place) for line in lines] == [(["灯塔", "小百合"], "海边营地"), ([], "")]
    snapshot = await store.archive.get_snapshot(live_session_id=session_id)
    assert snapshot is not None and snapshot.lines[0].line.keywords == ["灯塔", "小百合"]

    await store.execute("UPDATE archive_lines SET keywords = '不是 JSON' WHERE id = ?", (lines[0].id,))
    (broken,) = await store.archive.list_lines_by_ids(line_ids=[lines[0].id])
    assert (broken.keywords, broken.place) == ([], "海边营地")


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


async def test_commitment_to_a_player_from_game_chat_keeps_number_on_rerun(store: SQLiteDatabase) -> None:
    """对玩家的承诺：证据是她打在游戏公屏上的那句，按公屏行对齐编号；不再认定才删。"""
    session_id = await _ended_session(store)
    alex = await store.game_players.meet_player(
        game="minecraft", uuid="uuid-a", name="Alex", live_session_id=session_id, at_ms=1100
    )
    said = await store.game_players.add_game_chat(
        live_session_id=session_id, game="minecraft", direction="streamer", content="Alex 下次带你去挖矿", at_ms=1200
    )

    async def settle(commitments: list) -> dict:
        # 每轮先排回整理队列，模拟运营者点「重新整理」
        await store.sessions.requeue_settlement(live_session_id=session_id)
        return await store.sessions.apply_settlement(
            live_session_id=session_id,
            archive_lines=[],
            commitments=commitments,
            closures=[],
            settled_at_ms=9_999,
            now_ms_value=9_999,
        )

    first = PendingCommitment(
        what="带 Alex 去挖矿", quote="下次带你去挖矿", evidence_game_chat_id=said, target_player_id=alex.id
    )
    assert (await settle([first]))["commitments"] == 1
    (row,) = await store.commitments.list_commitments(statuses=["open"])
    assert (row.evidence_chat_id, row.evidence_game_chat_id, row.target_player_id) == (None, said, alex.id)

    # 重跑仍认定同一句：编号不变、标题原地更新
    renamed = first.model_copy(update={"what": "带他去挖矿"})
    await settle([renamed])
    (again,) = await store.commitments.list_commitments(statuses=["open"])
    assert (again.id, again.what) == (row.id, "带他去挖矿")

    # 不再认定：删掉
    await settle([])
    assert await store.commitments.list_commitments(statuses=["open"]) == []


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
    assert first == {
        "archive": 1,
        "snapshot": 1,
        "commitments": 1,
        "closed": 0,
        "claimed": 1,
        "candidates": 0,
        "player_candidates": 0,
        "social_facts": 0,
        "interactions": 0,
    }

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
    assert second == {
        "archive": 1,
        "snapshot": 1,
        "commitments": 1,
        "closed": 0,
        "claimed": 1,
        "candidates": 0,
        "player_candidates": 0,
        "social_facts": 0,
        "interactions": 0,
    }

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
    assert written == {
        "archive": 0,
        "snapshot": 0,
        "commitments": 0,
        "closed": 0,
        "claimed": 0,
        "candidates": 0,
        "player_candidates": 0,
        "social_facts": 0,
        "interactions": 0,
    }
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
