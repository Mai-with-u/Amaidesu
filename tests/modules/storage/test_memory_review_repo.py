"""记忆页人工审核仓储：改标题、标稳定、放回快照、新增一行、删行、标已审，每次都留改动记录。

人改过的行锁住（之后的整理不能去掉或替换），放回、新增的行进最新一份快照的最前面并标放回；
没存下快照、没整理完的场次、原话对不上台词时操作做不了，给出原因。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, List, Tuple

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import KeptSnapshotLine, PendingArchiveLine
from src.modules.storage.repos.memory_review import MAX_PINNED_LINES, MemoryReviewError


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "review.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _session(store: SQLiteDatabase, *, started_at_ms: int, kind: str = "live") -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 600_000)
    return session_id


async def _speech(store: SQLiteDatabase, session_id: int, content: str, *, dropped: bool = False) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=5,
        sender_role="assistant",
        content=content,
        message_type="text",
        dropped_at_ms=9 if dropped else None,
    )


async def _settle(
    store: SQLiteDatabase, session_id: int, new: List[PendingArchiveLine], kept: List[KeptSnapshotLine] | None = None
) -> None:
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=new,
        commitments=[],
        closures=[],
        settled_at_ms=9_999,
        now_ms_value=9_999,
        snapshot_kept=kept or [],
    )


async def _edits(store: SQLiteDatabase) -> List[Tuple[str, str, str, str]]:
    """改动记录（按写入先后）：测试里直接读表，只读不写。"""
    with store.manager.transaction() as conn:
        rows = conn.execute("SELECT target, action, before_text, after_text FROM memory_edits ORDER BY id").fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def _two_sessions(store: SQLiteDatabase) -> Tuple[int, int, int, int]:
    """第一场写两行、第二场只接住其中一行：返回 (第一场, 第二场, 被接住的行, 被挤出的行)。"""
    first = await _session(store, started_at_ms=1_000)
    await _settle(
        store,
        first,
        [
            PendingArchiveLine(text="给狼取名阿黄", quote="就叫阿黄", evidence_chat_id=1, lasting=True),
            PendingArchiveLine(text="挖到铁矿", quote="铁矿来了", evidence_chat_id=2),
        ],
    )
    kept_id, dropped_id = [line.id for line in await store.archive.list_lines(live_session_id=first)]
    second = await _session(store, started_at_ms=2_000_000)
    await _settle(store, second, [], [KeptSnapshotLine(archive_line_id=kept_id, lasting=True, seen_at_ms=1_000)])
    return first, second, kept_id, dropped_id


async def test_title_edit_locks_the_line_and_is_recorded(store: SQLiteDatabase) -> None:
    first, _second, kept_id, _dropped = await _two_sessions(store)

    assert await store.memory_review.edit_title(line_id=kept_id, text=" 狼叫阿黄 ", at_ms=10)
    assert not await store.memory_review.edit_title(line_id=kept_id, text="狼叫阿黄", at_ms=11), "没变不记"
    line = (await store.archive.list_lines(live_session_id=first))[0]
    assert (line.text, line.quote, line.human_locked) == ("狼叫阿黄", "就叫阿黄", True), "原话不变"
    assert await _edits(store) == [("archive_line", "title", "给狼取名阿黄", "狼叫阿黄")]

    with pytest.raises(MemoryReviewError) as empty:
        await store.memory_review.edit_title(line_id=kept_id, text="  ", at_ms=12)
    assert empty.value.kind == "invalid"
    with pytest.raises(MemoryReviewError) as missing:
        await store.memory_review.edit_title(line_id=9_999, text="随便", at_ms=12)
    assert missing.value.kind == "not_found"


async def test_lasting_only_for_lines_in_the_newest_snapshot(store: SQLiteDatabase) -> None:
    _first, second, kept_id, dropped_id = await _two_sessions(store)

    await store.memory_review.set_lasting(line_id=kept_id, lasting=False, at_ms=10)
    state = await store.memory_review.session_state(live_session_id=second)
    assert state.snapshot_session_id == second
    assert state.snapshot_lines[kept_id].lasting is False
    assert (await store.archive.list_lines_by_ids(line_ids=[kept_id]))[0].human_locked

    with pytest.raises(MemoryReviewError) as outside:
        await store.memory_review.set_lasting(line_id=dropped_id, lasting=True, at_ms=11)
    assert outside.value.kind == "conflict", "被挤出的行先放回再标"
    assert await _edits(store) == [("snapshot_line", "lasting", "1", "0")]


async def test_pin_puts_an_evicted_line_first_in_the_newest_snapshot(store: SQLiteDatabase) -> None:
    _first, second, kept_id, dropped_id = await _two_sessions(store)

    await store.memory_review.pin_line(line_id=dropped_id, at_ms=10)
    snapshot = await store.archive.get_snapshot(live_session_id=second)
    assert snapshot is not None
    assert [(item.line.id, item.pinned, item.lasting) for item in snapshot.lines] == [
        (dropped_id, True, False),
        (kept_id, False, True),
    ], "放回的行排最前，稳定与否沿用它上一次在快照里的标记"
    latest = await store.archive.latest_snapshot()
    assert latest is not None and latest.lines[0].line.id == dropped_id, "常驻简报读到的就是这一份"
    assert (await store.memory_review.session_state(live_session_id=second)).pinned_count == 1

    with pytest.raises(MemoryReviewError) as again:
        await store.memory_review.pin_line(line_id=dropped_id, at_ms=11)
    assert again.value.kind == "conflict"
    assert await _edits(store) == [("snapshot_line", "pin", "", "挖到铁矿")]


async def test_unpin_turns_a_pinned_row_back_into_a_normal_snapshot_row(store: SQLiteDatabase) -> None:
    """取消放回：这一行还在快照里，只是不再标放回（之后按规则取舍），记一条改动记录；不是放回的行报 conflict。"""
    _first, second, kept_id, dropped_id = await _two_sessions(store)
    await store.memory_review.pin_line(line_id=dropped_id, at_ms=10)

    await store.memory_review.unpin_line(line_id=dropped_id, at_ms=11)
    state = await store.memory_review.session_state(live_session_id=second)
    assert (state.snapshot_lines[dropped_id].pinned, state.pinned_count) == (False, 0)
    for line_id in (dropped_id, kept_id):
        with pytest.raises(MemoryReviewError) as refused:
            await store.memory_review.unpin_line(line_id=line_id, at_ms=12)
        assert refused.value.kind == "conflict"
    assert await _edits(store) == [
        ("snapshot_line", "pin", "", "挖到铁矿"),
        ("snapshot_line", "unpin", "挖到铁矿", ""),
    ]


async def test_pinned_rows_are_capped_at_four_until_one_is_unpinned(store: SQLiteDatabase) -> None:
    """放回与新增的行一份快照至多 4 行：满了再放回、再新增都报 conflict，取消一条后又能放回。"""
    _first, second, _kept, dropped_id = await _two_sessions(store)
    added = []
    for index in range(MAX_PINNED_LINES):
        said = await _speech(store, second, f"第{index}只羊叫小{index}号")
        added.append(
            await store.memory_review.add_line(
                live_session_id=second,
                evidence_chat_id=said,
                quote=f"小{index}号",
                text=f"羊{index}",
                lasting=False,
                at_ms=10,
            )
        )
    assert (await store.memory_review.session_state(live_session_id=second)).pinned_count == MAX_PINNED_LINES

    extra = await _speech(store, second, "狗叫豆豆")
    with pytest.raises(MemoryReviewError) as full_add:
        await store.memory_review.add_line(
            live_session_id=second, evidence_chat_id=extra, quote="豆豆", text="狗叫豆豆", lasting=False, at_ms=11
        )
    with pytest.raises(MemoryReviewError) as full_pin:
        await store.memory_review.pin_line(line_id=dropped_id, at_ms=12)
    assert (full_add.value.kind, full_pin.value.kind) == ("conflict", "conflict")
    assert "先取消一条放回" in full_pin.value.message

    await store.memory_review.unpin_line(line_id=added[0], at_ms=13)
    await store.memory_review.pin_line(line_id=dropped_id, at_ms=14)
    assert (await store.memory_review.session_state(live_session_id=second)).pinned_count == MAX_PINNED_LINES


async def test_add_line_takes_a_verbatim_quote_from_her_broadcast_speech(store: SQLiteDatabase) -> None:
    _first, second, _kept, _dropped = await _two_sessions(store)
    said = await _speech(store, second, "以后这只猫就叫  团子了")
    dropped = await _speech(store, second, "没送到的那句", dropped=True)

    new_id = await store.memory_review.add_line(
        live_session_id=second, evidence_chat_id=said, quote="这只猫就叫 团子", text="猫叫团子", lasting=True, at_ms=10
    )
    line = (await store.archive.list_lines_by_ids(line_ids=[new_id]))[0]
    assert (line.live_session_id, line.quote, line.human_locked, line.game) == (second, "这只猫就叫 团子", True, None)
    snapshot = await store.archive.get_snapshot(live_session_id=second)
    assert snapshot is not None and (
        snapshot.lines[0].line.id,
        snapshot.lines[0].pinned,
        snapshot.lines[0].lasting,
    ) == (
        new_id,
        True,
        True,
    )

    for evidence, quote, kind in (
        (said, "这只狗", "invalid"),  # 原话对不上
        (dropped, "没送到", "invalid"),  # 没播出去的不算她说过
        (said, "团子", "conflict"),  # 同一句已经写成一行
    ):
        with pytest.raises(MemoryReviewError) as refused:
            await store.memory_review.add_line(
                live_session_id=second, evidence_chat_id=evidence, quote=quote, text="标题", lasting=False, at_ms=11
            )
        assert refused.value.kind == kind, quote
    assert await _edits(store) == [("archive_line", "add", "", "猫叫团子")]


async def test_add_line_waits_for_settlement_and_for_a_stored_snapshot(store: SQLiteDatabase) -> None:
    pending = await _session(store, started_at_ms=1_000)
    said = await _speech(store, pending, "我们的基地叫蘑菇屋")
    with pytest.raises(MemoryReviewError) as unsettled:
        await store.memory_review.add_line(
            live_session_id=pending, evidence_chat_id=said, quote="蘑菇屋", text="基地叫蘑菇屋", lasting=False, at_ms=1
        )
    assert unsettled.value.kind == "conflict", "没整理完的场次整理时会整场替换，人加的行会被冲掉"

    # 滚动之前的旧场次：有档案行、没存下快照
    await store.archive.insert_line(live_session_id=pending, text="旧事", quote="旧事原话", evidence_chat_id=said)
    assert (await store.memory_review.session_state(live_session_id=pending)).snapshot_session_id is None
    with pytest.raises(MemoryReviewError) as legacy:
        await store.memory_review.pin_line(
            line_id=(await store.archive.list_lines(live_session_id=pending))[0].id, at_ms=2
        )
    assert legacy.value.kind == "conflict"


async def test_delete_and_reviewed_are_recorded_and_resettle_sees_human_edits(store: SQLiteDatabase) -> None:
    first, second, kept_id, dropped_id = await _two_sessions(store)
    assert await store.memory_review.count_human_edits(live_session_id=first) == 0

    await store.memory_review.edit_title(line_id=kept_id, text="狼叫阿黄", at_ms=10)
    await store.memory_review.pin_line(line_id=dropped_id, at_ms=11)
    assert await store.memory_review.count_human_edits(live_session_id=first) == 1, "本场锁住的行"
    assert await store.memory_review.count_human_edits(live_session_id=second) == 1, "本场快照里放回的行"

    assert await store.memory_review.delete_line(line_id=kept_id, at_ms=12)
    assert not await store.memory_review.delete_line(line_id=kept_id, at_ms=13)
    await store.memory_review.mark_reviewed(live_session_id=second, at_ms=14)
    state = await store.memory_review.session_state(live_session_id=second)
    assert state.reviewed_at_ms == 14
    assert await _edits(store) == [
        ("archive_line", "title", "", ""),  # 行被删了：改动记录里的文本跟着清空
        ("snapshot_line", "pin", "", "挖到铁矿"),
        ("archive_line", "delete", "", ""),
        ("session", "reviewed", "", ""),
    ]

    rehearsal = await _session(store, started_at_ms=3_000_000, kind="rehearsal")
    with pytest.raises(MemoryReviewError) as refused:
        await store.memory_review.mark_reviewed(live_session_id=rehearsal, at_ms=15)
    assert refused.value.kind == "conflict"
