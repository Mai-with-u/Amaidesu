"""记忆检索派生索引的仓储：收什么、不收什么，写回前再核一次，改彩排时删掉那一场的索引。

收的口径全在仓储里定：台词只收正式场里她播出满 30 秒的、只取她自己的正文；游戏事件只收里程碑
与阶段汇报（白名单）；判了误记的承诺不收。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, List

import numpy as np
import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import IndexDoc
from src.modules.storage.repos.memory_index import SPEECH_SETTLE_MS, text_hash

NOW_MS = 1_791_000_000_000


def _tokens(document: str) -> str:
    """测试里只需要一串能写进全文表的词，不关心切法。"""
    return document.replace("\n", " ")


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    database = SQLiteDatabase(tmp_path / "index.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _session(store: SQLiteDatabase, *, kind: str = "live", start_ms: int = NOW_MS - 3_600_000) -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=start_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=start_ms + 600_000)
    return session_id


async def _speech(
    store: SQLiteDatabase, session_id: int, content: str, *, age_ms: int = 60_000, **extra: object
) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=NOW_MS - age_ms,
        sender_role="assistant",
        content=content,
        message_type="speak",
        **extra,  # type: ignore[arg-type]
    )


def _ids(docs: List[IndexDoc]) -> List[int]:
    return [doc.source_id for doc in docs]


async def test_speech_index_takes_only_her_aired_lines_in_formal_sessions(store: SQLiteDatabase) -> None:
    live = await _session(store)
    rehearsal = await _session(store, kind="rehearsal")
    danmaku = await store.chat.insert_live_chat(
        live_session_id=live,
        timestamp_ms=NOW_MS - 90_000,
        sender_role="viewer",
        content="观众弹幕原文",
        message_type="danmaku",
        message_id="m1",
    )
    aired = await _speech(store, live, "芝麻又趴在熔炉边上", reply_to_message_id="m1")
    await _speech(store, live, "这句被打断了", dropped_at_ms=NOW_MS - 50_000)
    await _speech(store, live, "刚说完还没满三十秒", age_ms=SPEECH_SETTLE_MS - 1)
    await _speech(store, rehearsal, "彩排里说的话")

    pending = await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50)

    assert _ids(pending) == [aired], "只收正式场里她播出满 30 秒的台词"
    assert pending[0].document == "芝麻又趴在熔炉边上", "只取她自己的正文，不带她回应的那条弹幕"
    assert danmaku not in _ids(pending)


async def test_game_event_index_is_a_whitelist_of_milestones_and_reports(store: SQLiteDatabase) -> None:
    live = await _session(store)
    kept = []
    for event_type in ("milestone", "report", "attention_required", "error", "chat", "player_body_language"):
        event_id = await store.events.insert_game_event(
            live_session_id=live, game="minecraft", event_type=event_type, message=f"{event_type} 的正文"
        )
        if event_type in ("milestone", "report"):
            kept.append(event_id)

    pending = await store.memory_index.pending_full_text(source="event", now_ms=NOW_MS, limit=50)

    assert _ids(pending) == kept


async def test_voided_commitments_are_not_indexed(store: SQLiteDatabase) -> None:
    live = await _session(store)
    kept = await store.commitments.insert_commitment(what="唱首歌", quote="下次唱首歌", created_session_id=live)
    voided = await store.commitments.insert_commitment(what="记错的事", quote="记错了", created_session_id=live)
    assert kept is not None and voided is not None
    await store.commitments.void_commitment(commitment_id=voided)

    pending = await store.memory_index.pending_full_text(source="commitment", now_ms=NOW_MS, limit=50)

    assert _ids(pending) == [kept]
    assert pending[0].document == "唱首歌\n下次唱首歌"


async def test_written_rows_leave_the_pending_list_and_are_searchable(store: SQLiteDatabase) -> None:
    live = await _session(store)
    line_id = await store.archive.insert_line(
        live_session_id=live, text="猫取名芝麻", quote="就叫芝麻", evidence_chat_id=1
    )
    (doc,) = await store.memory_index.pending_full_text(source="archive", now_ms=NOW_MS, limit=50)

    assert (
        await store.memory_index.write_full_text(source="archive", items=[(doc, "猫取 取名 名芝 芝麻 就叫 叫芝 芝麻")])
        == 1
    )

    assert await store.memory_index.pending_full_text(source="archive", now_ms=NOW_MS, limit=50) == []
    rows = await store.execute("SELECT rowid FROM memory_fts_archive WHERE memory_fts_archive MATCH '\"芝麻\"'")
    assert [row[0] for row in rows] == [line_id]


async def test_rows_changed_between_read_and_write_are_skipped(store: SQLiteDatabase) -> None:
    """读出之后台词被标成没播出、正文被改（删观众替换昵称）：这一轮不写，下一轮按新正文重算。"""
    live = await _session(store)
    dropped = await _speech(store, live, "这句后来被标成没播出")
    renamed = await _speech(store, live, "欢迎小明")
    docs = await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50)
    await store.execute("UPDATE live_chat SET dropped_at_ms = ? WHERE id = ?", (NOW_MS, dropped))
    await store.execute("UPDATE live_chat SET content = '欢迎一位观众' WHERE id = ?", (renamed,))

    written = await store.memory_index.write_full_text(
        source="speech", items=[(doc, _tokens(doc.document)) for doc in docs]
    )
    vectors = await store.memory_index.write_vectors(
        source="speech", model_key="m", items=[(doc, [0.5, 0.25]) for doc in docs]
    )

    assert (written, vectors) == (0, 0)
    (fresh,) = await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50)
    assert (fresh.source_id, fresh.document) == (renamed, "欢迎一位观众")


async def test_vectors_are_little_endian_float32_and_redone_when_the_model_changes(store: SQLiteDatabase) -> None:
    live = await _session(store)
    await store.archive.insert_line(live_session_id=live, text="猫取名芝麻", quote="就叫芝麻", evidence_chat_id=1)
    (doc,) = await store.memory_index.pending_vectors(source="archive", model_key="bge-m3", now_ms=NOW_MS, limit=50)

    await store.memory_index.write_vectors(source="archive", model_key="bge-m3", items=[(doc, [0.5, -1.0, 2.0])])

    (row,) = await store.execute("SELECT model, dim, text_hash, vector FROM memory_vectors_archive")
    assert (row["model"], row["dim"], row["text_hash"]) == ("bge-m3", 3, text_hash(doc.document))
    assert np.frombuffer(row["vector"], dtype="<f4").tolist() == [0.5, -1.0, 2.0]
    assert await store.memory_index.pending_vectors(source="archive", model_key="bge-m3", now_ms=NOW_MS, limit=50) == []
    redo = await store.memory_index.pending_vectors(source="archive", model_key="bge-m3@512", now_ms=NOW_MS, limit=50)
    assert _ids(redo) == [doc.source_id], "换模型或换维度后整表重算"


async def test_switching_a_session_to_rehearsal_drops_its_speech_and_event_index(store: SQLiteDatabase) -> None:
    """改彩排时台词与事件行本身不变、触发器拦不住：改类型的同一个事务里删掉那一场的索引行。"""
    switched = await _session(store)
    other = await _session(store)
    for session_id in (switched, other):
        await _speech(store, session_id, f"第 {session_id} 场的台词")
        await store.events.insert_game_event(
            live_session_id=session_id, game="minecraft", event_type="milestone", message=f"第 {session_id} 场的进展"
        )
    for source in ("speech", "event"):
        docs = await store.memory_index.pending_full_text(source=source, now_ms=NOW_MS, limit=50)
        await store.memory_index.write_full_text(source=source, items=[(doc, _tokens(doc.document)) for doc in docs])
        await store.memory_index.write_vectors(source=source, model_key="m", items=[(doc, [1.0]) for doc in docs])

    await store.sessions.set_live_session_kind_with_ledger(
        live_session_id=switched, kind="rehearsal", updated_at_ms=NOW_MS
    )

    for fts, vectors, key, table in (
        ("memory_fts_speech", "memory_vectors_speech", "chat_id", "live_chat"),
        ("memory_fts_event", "memory_vectors_event", "event_id", "game_events"),
    ):
        fts_sessions = await store.execute(
            f"SELECT t.live_session_id FROM {fts} f JOIN {table} t ON t.id = f.rowid"  # noqa: S608
        )
        vector_sessions = await store.execute(
            f"SELECT t.live_session_id FROM {vectors} v JOIN {table} t ON t.id = v.{key}"  # noqa: S608
        )
        assert [row[0] for row in fts_sessions] == [other]
        assert [row[0] for row in vector_sessions] == [other]

    # 改回正式场：那一场的行重新变成该收的，由索引任务补回来
    await store.sessions.set_live_session_kind_with_ledger(live_session_id=switched, kind="live", updated_at_ms=NOW_MS)
    pending = await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50)
    assert [doc.document for doc in pending] == [f"第 {switched} 场的台词"]
