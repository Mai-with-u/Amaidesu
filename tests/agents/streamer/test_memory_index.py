"""记忆索引任务：启动补算、先全文后向量、嵌入失败退避而全文照常、整理写完叫醒补一轮。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from src.agents.streamer.memory.index import BACKOFF_BASE_MS, MemoryIndexer
from src.modules.storage.database import SQLiteDatabase
from tests.config.helpers import wait_until
from tests.helpers import FakeClock
from tests.modules.llm.fakes import FakeEmbeddingServer


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    database = SQLiteDatabase(tmp_path / "index.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _seed(store: SQLiteDatabase, clock: FakeClock) -> int:
    """一场正式场：一行档案、一条承诺、一句播出满 30 秒的台词、一条里程碑。"""
    session_id = await store.sessions.insert_live_session(started_at_ms=clock.now_ms() - 3_600_000, kind="live")
    await store.archive.insert_line(live_session_id=session_id, text="猫取名芝麻", quote="就叫芝麻", evidence_chat_id=1)
    await store.commitments.insert_commitment(what="唱首歌", quote="下次唱首歌", created_session_id=session_id)
    await _speech(store, clock, session_id, "芝麻又趴在熔炉边上")
    await store.events.insert_game_event(
        live_session_id=session_id, game="minecraft", event_type="milestone", message="家门口挖好了水渠"
    )
    return session_id


async def _speech(store: SQLiteDatabase, clock: FakeClock, session_id: int, content: str) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=clock.now_ms() - 60_000,
        sender_role="assistant",
        content=content,
        message_type="speak",
    )


async def _count(store: SQLiteDatabase, table: str) -> int:
    (row,) = await store.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 测试常量表名
    return int(row[0])


async def test_first_pass_backfills_full_text_and_skips_vectors_without_embedding(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    await _seed(store, clock)
    indexer = MemoryIndexer(repo=store.memory_index, embedding=None, embedding_note="语义检索没开", clock=clock.now_ms)

    await indexer.run_once()

    for source in ("archive", "commitment", "speech", "event"):
        assert await _count(store, f"memory_fts_{source}") == 1
        assert await _count(store, f"memory_vectors_{source}") == 0
    rows = await store.execute("SELECT rowid FROM memory_fts_speech WHERE memory_fts_speech MATCH '\"芝麻\"'")
    assert len(rows) == 1, "两字切分写进去的词能按两个字的词查到"
    assert indexer.semantic_note == "语义检索没开"


async def test_vectors_follow_full_text_when_embedding_is_configured(
    store: SQLiteDatabase, loguru_capture: Any
) -> None:
    """配了嵌入：全文补完接着补向量；每轮补了多少记一行，没东西补的轮次不记 info。"""
    clock = FakeClock()
    await _seed(store, clock)
    server = FakeEmbeddingServer()
    indexer = MemoryIndexer(
        repo=store.memory_index, embedding=server.client(), embedding_note="开着", clock=clock.now_ms
    )

    await indexer.run_once()
    await indexer.run_once()

    passes = [record["message"] for record in loguru_capture.records if "记忆索引这一轮补了" in record["message"]]
    assert passes == [
        "记忆索引这一轮补了 全文：档案 1、承诺 1、台词 1、游戏事件 1；向量：档案 1、承诺 1、台词 1、游戏事件 1"
    ]

    for source in ("archive", "commitment", "speech", "event"):
        assert await _count(store, f"memory_vectors_{source}") == 1
    assert sorted(text for batch in server.requests for text in batch) == sorted(
        ["猫取名芝麻\n就叫芝麻", "唱首歌\n下次唱首歌", "芝麻又趴在熔炉边上", "家门口挖好了水渠"]
    ), "第二轮没有缺的，不再请求"


async def test_embedding_failure_backs_off_while_full_text_keeps_going(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    session_id = await _seed(store, clock)
    server = FakeEmbeddingServer()
    server.fail = True
    indexer = MemoryIndexer(
        repo=store.memory_index, embedding=server.client(), embedding_note="开着", clock=clock.now_ms
    )

    await indexer.run_once()
    assert len(server.requests) == 1, "第一批失败后这一轮不再请求别的来源"
    assert "退避" in indexer.semantic_note

    # 退避期间：新台词照样进全文，嵌入服务不再被请求
    await _speech(store, clock, session_id, "水渠边上种了甘蔗")
    clock.tick(10_000)
    await indexer.run_once()
    assert await _count(store, "memory_fts_speech") == 2
    assert len(server.requests) == 1

    # 退避期满、服务恢复：补上全部向量，状态回到"开着"
    server.fail = False
    clock.tick(BACKOFF_BASE_MS)
    await indexer.run_once()
    assert await _count(store, "memory_vectors_speech") == 2
    assert indexer.semantic_note == "开着"


async def test_background_loop_backfills_on_start_and_runs_again_when_woken(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    session_id = await _seed(store, clock)
    indexer = MemoryIndexer(
        repo=store.memory_index,
        embedding=None,
        embedding_note="语义检索没开",
        clock=clock.now_ms,
        interval_ms=60_000,
    )
    await indexer.start()
    try:
        await wait_until(lambda: indexer.passes >= 1, what="启动后的第一轮补算")
        assert await _count(store, "memory_fts_archive") == 1

        # 整理写完叫醒：不等下一个间隔（这里设成一分钟）就补一轮
        await store.archive.insert_line(
            live_session_id=session_id, text="挖好了水渠", quote="挖好了水渠", evidence_chat_id=2
        )
        indexer.request_run()
        await wait_until(lambda: indexer.passes >= 2, what="叫醒后的一轮")
        assert await _count(store, "memory_fts_archive") == 2
    finally:
        await indexer.stop()
