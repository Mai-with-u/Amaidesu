"""线索卡记录仓储：写一批、回填用上的时刻、按场读；指向的条目或场次被删时记录跟着删。

另测档案行的线索池查询（被动想起的规则与地点两路从这里取候选）：只取正式场、只取写了触发词或
挂了地点的行，给了编号就只在这些行里取。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import NewCueLogEntry


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "cue_log.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _session(store: SQLiteDatabase, *, kind: str = "live", started_at_ms: int = 1_000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 600_000)
    return session_id


async def _line(store: SQLiteDatabase, session_id: int, text: str, **extra: object) -> int:
    line_id = await store.archive.insert_line(
        live_session_id=session_id,
        text=text,
        quote=f"{text}的原话",
        evidence_chat_id=1,
        **extra,  # type: ignore[arg-type]
    )
    assert line_id
    return line_id


async def test_entries_round_trip_and_used_time_backfills_once(store: SQLiteDatabase) -> None:
    """推出去的卡原样读回；用上时刻只回填本场、推送早于那一刻、还没回填的记录。"""
    session_id = await _session(store)
    other_session = await _session(store, started_at_ms=2_000_000)
    line_id = await _line(store, session_id, "女仆取名团子", keywords=["团子"])
    commitment_id = await store.commitments.insert_commitment(what="教大家搭电梯", quote="下次教你们搭电梯")
    assert commitment_id is not None
    written = await store.memory_cue_log.insert_entries(
        entries=[
            NewCueLogEntry(
                live_session_id=session_id,
                pushed_at_ms=100,
                card_kind="rule",
                archive_line_id=line_id,
                sources=["danmaku", "self"],
                detail={"keywords": ["团子"], "how": "exact"},
            ),
            NewCueLogEntry(
                live_session_id=session_id,
                pushed_at_ms=200,
                card_kind="vector",
                commitment_id=commitment_id,
                sources=["self"],
                detail={"cosine": 0.71, "rank": 1},
                self_triggered=True,
            ),
            NewCueLogEntry(live_session_id=other_session, pushed_at_ms=100, card_kind="rule", archive_line_id=line_id),
        ]
    )
    assert written == 3

    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert [(entry.card_kind, entry.archive_line_id, entry.commitment_id) for entry in entries] == [
        ("rule", line_id, None),
        ("vector", None, commitment_id),
    ]
    assert entries[0].sources == ["danmaku", "self"] and entries[0].detail == {"keywords": ["团子"], "how": "exact"}
    assert entries[1].self_triggered is True and entries[0].used_at_ms is None

    # 推送晚于检测时刻的不算：还没推就被点名用上的条目不是卡片的功劳
    assert (
        await store.memory_cue_log.mark_used(live_session_id=session_id, commitment_ids=[commitment_id], used_at_ms=150)
        == 0
    )
    assert (
        await store.memory_cue_log.mark_used(
            live_session_id=session_id, archive_line_ids=[line_id], commitment_ids=[commitment_id], used_at_ms=300
        )
        == 2
    )
    assert (
        await store.memory_cue_log.mark_used(live_session_id=session_id, archive_line_ids=[line_id], used_at_ms=900)
        == 0
    )
    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert [entry.used_at_ms for entry in entries] == [300, 300]
    others = await store.memory_cue_log.list_session_entries(live_session_id=other_session)
    assert [entry.used_at_ms for entry in others] == [None], "别的场次的记录不回填"


async def test_entries_follow_their_item_and_session_away(store: SQLiteDatabase) -> None:
    """指向的档案行被删、场次被删时，对应记录跟着删（外键级联）。"""
    session_id = await _session(store)
    kept = await _line(store, session_id, "水车搭好了")
    deleted = await _line(store, session_id, "答应过画头像")
    await store.memory_cue_log.insert_entries(
        entries=[
            NewCueLogEntry(live_session_id=session_id, pushed_at_ms=1, card_kind="rule", archive_line_id=kept),
            NewCueLogEntry(live_session_id=session_id, pushed_at_ms=2, card_kind="rule", archive_line_id=deleted),
            NewCueLogEntry(live_session_id=session_id, pushed_at_ms=3, card_kind="joke", detail={"joke": "倒立洗头"}),
        ]
    )

    assert await store.archive.delete_line(line_id=deleted) is True
    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert [entry.archive_line_id for entry in entries] == [kept, None]

    assert await store.sessions.delete_live_session(live_session_id=session_id) is True
    assert await store.memory_cue_log.list_session_entries(live_session_id=session_id) == []


async def test_cue_pool_takes_formal_session_lines_with_keywords_or_place(store: SQLiteDatabase) -> None:
    """线索池只取正式场里写了触发词或挂了地点的档案行（新的在前）；给了编号就只核那几行。"""
    live = await _session(store)
    rehearsal = await _session(store, kind="rehearsal", started_at_ms=900_000)
    plain = await _line(store, live, "聊了最近的动画")
    keyword_line = await _line(store, live, "女仆取名团子", keywords=["团子"])
    place_line = await _line(store, live, "北边农场种下小麦", game="minecraft", save_id="a", place="北边农场")
    rehearsal_line = await _line(store, rehearsal, "彩排试了新皮肤", keywords=["新皮肤"])

    pool = await store.archive.list_cue_lines()
    assert [line.id for line in pool] == [place_line, keyword_line]
    assert pool[0].place == "北边农场" and pool[1].keywords == ["团子"]

    checked = await store.archive.list_cue_lines(line_ids=[plain, rehearsal_line, keyword_line])
    assert [line.id for line in checked] == [keyword_line, plain], "彩排场的行不推"
    assert await store.archive.list_cue_lines(line_ids=[]) == []
    assert [line.id for line in await store.archive.list_cue_lines(limit=1)] == [place_line]
