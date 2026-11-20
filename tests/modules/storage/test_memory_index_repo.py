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
    # 委派账（delegation_*）是给下播核对用的指令原文与终态，不是游戏里发生的事，同样不收
    types = ("milestone", "report", "attention_required", "error", "chat", "player_body_language")
    for event_type in (*types, "delegation_accepted", "delegation_succeeded"):
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


async def test_speech_window_rows_resolves_adjacent_speech_while_keeping_single_line_document_hash(
    store: SQLiteDatabase,
) -> None:
    """台词正本 document 与向量 text_hash 保持单句原样，speech_window_rows 单独取同场前 30 秒合格台词且排除弹幕与废稿。"""
    earlier_session = await _session(store, start_ms=NOW_MS - 7_200_000)
    live = await _session(store)

    await _speech(store, earlier_session, "上一场末尾说的话", age_ms=125_000)
    too_early = await _speech(store, live, "四十秒前聊别的", age_ms=120_000)
    first_cause = await _speech(store, live, "北边刚摆好高炉", age_ms=80_000)
    # 中间夹入观众弹幕与未送达废稿，均不得进入邻句窗口，也不阻断主播自身前后紧邻关系
    await store.chat.insert_live_chat(
        live_session_id=live,
        timestamp_ms=NOW_MS - 75_000,
        sender_role="viewer",
        content="观众插话绝不能进跨场索引",
        message_type="danmaku",
        message_id="dm-mid",
    )
    await _speech(store, live, "半截打断废稿", age_ms=72_000, dropped_at_ms=NOW_MS - 71_000)
    second_effect = await _speech(store, live, "结果蒸汽管接反了", age_ms=65_000)

    pending = await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50)
    by_id = {doc.source_id: doc for doc in pending}
    vec_docs = {
        doc.source_id: doc
        for doc in await store.memory_index.pending_vectors(source="speech", model_key="m", now_ms=NOW_MS, limit=50)
    }

    assert by_id[first_cause].document == "北边刚摆好高炉"
    assert by_id[second_effect].document == "结果蒸汽管接反了", "单句正本不拼邻句，避免改动向量 text_hash"
    assert vec_docs[second_effect].document == "结果蒸汽管接反了"

    windows = await store.memory_index.speech_window_rows(chat_ids=[too_early, first_cause, second_effect])
    assert windows[too_early] == ("四十秒前聊别的", None, "")
    assert windows[first_cause] == ("北边刚摆好高炉", None, ""), "距上一句满 40 秒（>30 秒），不往前拼"
    assert windows[second_effect] == ("结果蒸汽管接反了", first_cause, "北边刚摆好高炉")

    assert await store.memory_index.pending_total(model_key="m", now_ms=NOW_MS) == len(pending)
    assert await store.memory_index.write_full_text(
        source="speech", items=[(doc, _tokens(doc.document)) for doc in pending]
    ) == len(pending)
    assert await store.memory_index.write_vectors(
        source="speech", model_key="m", items=[(doc, [1.0, 0.0]) for doc in pending]
    ) == len(pending)
    (vec_row,) = await store.execute("SELECT text_hash FROM memory_vectors_speech WHERE chat_id = ?", (second_effect,))
    assert vec_row["text_hash"] == text_hash("结果蒸汽管接反了")
    assert await store.memory_index.pending_total(model_key="m", now_ms=NOW_MS) == 0


async def test_blank_speech_and_newline_only_documents_never_enter_vector_queue(store: SQLiteDatabase) -> None:
    """空白台词与拼出来只有换行符的空记录不进向量待补队列，避免空串打断整轮嵌入补算。"""
    live = await _session(store)
    await _speech(store, live, "   \n\t  ", age_ms=60_000)
    await store.archive.insert_line(live_session_id=live, text="  ", quote="\t", evidence_chat_id=1)

    assert await store.memory_index.pending_full_text(source="speech", now_ms=NOW_MS, limit=50) == []
    assert await store.memory_index.pending_vectors(source="speech", model_key="m", now_ms=NOW_MS, limit=50) == []
    assert await store.memory_index.pending_vectors(source="archive", model_key="m", now_ms=NOW_MS, limit=50) == []
    # archive 全文行写完后，因正文剥除空白与换行后为空，pending_total 归零，不会卡在缺向量里
    archive_docs = await store.memory_index.pending_full_text(source="archive", now_ms=NOW_MS, limit=50)
    await store.memory_index.write_full_text(source="archive", items=[(doc, "") for doc in archive_docs])
    assert await store.memory_index.pending_total(model_key="m", now_ms=NOW_MS) == 0


async def test_stored_vector_matrix_caches_normalized_matrix_and_invalidates_on_all_mutations(
    store: SQLiteDatabase,
) -> None:
    """进程内向量矩阵缓存：重复查询与单场切片复用归一化矩阵；写向量、改彩排清索引与外部改动均精准失效。"""
    s1 = await _session(store, start_ms=NOW_MS - 7_200_000)
    s2 = await _session(store, start_ms=NOW_MS - 3_600_000)
    c1 = await _speech(store, s1, "第一场挖到了很多铁矿")
    c2 = await _speech(store, s2, "第二场搭好了刷铁机")
    docs = await store.memory_index.pending_vectors(source="speech", model_key="m", now_ms=NOW_MS, limit=10)
    by_id = {doc.source_id: doc for doc in docs}
    await store.memory_index.write_vectors(
        source="speech",
        model_key="m",
        items=[(by_id[c1], [3.0, 4.0]), (by_id[c2], [0.0, 2.0])],
    )

    # 首次全量查询触发 1 次矩阵构建并归一化；后续重复查与按场次切片均直接命中缓存
    first = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert first.source_ids == (c1, c2)
    assert first.matrix.dtype == np.float32
    assert np.allclose(first.matrix[0], [0.6, 0.8])
    assert np.allclose(first.matrix[1], [0.0, 1.0])
    assert store.memory_index.vector_cache_loads == 1

    second = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert second is first
    assert store.memory_index.vector_cache_loads == 1

    only_s1 = await store.memory_index.stored_vector_matrix(source="speech", model_key="m", live_session_id=s1)
    assert only_s1.source_ids == (c1,)
    assert np.allclose(only_s1.matrix[0], [0.6, 0.8])
    assert store.memory_index.vector_cache_loads == 1

    # 写入新向量后自动失效，下一次全量查询看到新向量
    c3 = await _speech(store, s2, "第二场又种了小麦")
    (doc3,) = await store.memory_index.pending_vectors(source="speech", model_key="m", now_ms=NOW_MS, limit=10)
    assert doc3.source_id == c3
    await store.memory_index.write_vectors(source="speech", model_key="m", items=[(doc3, [1.0, 0.0])])
    after_write = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert after_write.source_ids == (c1, c2, c3)
    assert store.memory_index.vector_cache_loads == 2

    # 场次改彩排走 delete_session_index_rows 清索引：精准失效，不再返回该场向量
    await store.sessions.set_live_session_kind_with_ledger(live_session_id=s1, kind="rehearsal", updated_at_ms=NOW_MS)
    after_rehearsal = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert after_rehearsal.source_ids == (c2, c3)
    assert store.memory_index.vector_cache_loads == 3

    # 绕过本仓储直接标台词为未播出（由触发器或 eligible 条件过滤）：轻量指纹立即识破，绝无脏缓存
    await store.execute("UPDATE live_chat SET dropped_at_ms = ? WHERE id = ?", (NOW_MS, c2))
    after_drop = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert after_drop.source_ids == (c3,)
    assert store.memory_index.vector_cache_loads == 4

    # 外部直接 SQL 原位改写 text_hash（保持前 3 位相同、仅改动第 10 位）也能被 12 位指纹立即感知失效
    (orig_row,) = await store.execute("SELECT text_hash FROM memory_vectors_speech WHERE chat_id = ?", (c3,))
    orig_hash = str(orig_row["text_hash"])
    flipped_char = "0" if orig_hash[9] != "0" else "1"
    mutated_hash = orig_hash[:9] + flipped_char + orig_hash[10:]
    await store.execute("UPDATE memory_vectors_speech SET text_hash = ? WHERE chat_id = ?", (mutated_hash, c3))
    after_hash_mutation = await store.memory_index.stored_vector_matrix(source="speech", model_key="m")
    assert after_hash_mutation.source_ids == (c3,)
    assert store.memory_index.vector_cache_loads == 5
