"""混合检索：关键词按 BM25、子串兜底、语义按余弦，RRF 融合；查询时只查正式场；嵌入不可用退回关键词。"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.search import RRF_K, MemorySearch
from src.modules.llm.embedding import EmbeddingClient
from src.modules.storage.database import SQLiteDatabase
from tests.helpers import FakeClock
from tests.modules.llm.fakes import FakeEmbeddingServer


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    database = SQLiteDatabase(tmp_path / "search.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def _session(store: SQLiteDatabase, clock: FakeClock) -> int:
    return await store.sessions.insert_live_session(started_at_ms=clock.now_ms() - 3_600_000, kind="live")


async def _say(store: SQLiteDatabase, clock: FakeClock, session_id: int, content: str) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=clock.now_ms() - 60_000,
        sender_role="assistant",
        content=content,
        message_type="speak",
    )


async def _index(store: SQLiteDatabase, clock: FakeClock, embedding: EmbeddingClient | None = None) -> None:
    await MemoryIndexer(repo=store.memory_index, embedding=embedding, embedding_note="", clock=clock.now_ms).run_once()


async def test_keyword_path_ranks_more_shared_words_first_and_respects_the_session(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    first = await _session(store, clock)
    later = await _session(store, clock)
    both = await _say(store, clock, first, "芝麻趴在熔炉边取暖")
    one = await _say(store, clock, first, "熔炉点上了")
    await _say(store, clock, first, "今天去找甘蔗")
    elsewhere = await _say(store, clock, later, "熔炉又灭了")
    await _index(store, clock)
    search = MemorySearch(repo=store.memory_index, embedding=None)

    hits = await search.search("芝麻 熔炉", sources=("speech",))
    assert [hit.source_id for hit in hits][:1] == [both]
    assert {hit.source_id for hit in hits} == {both, one, elsewhere}
    assert hits[0].score == pytest.approx(1 / (RRF_K + 1)) and hits[0].vector_rank is None

    only_first = await search.search("熔炉", sources=("speech",), live_session_id=first)
    assert {hit.source_id for hit in only_first} == {both, one}


async def test_rehearsal_rows_never_come_back_even_if_index_rows_were_left(store: SQLiteDatabase) -> None:
    """查询时再按场次类型核一遍：索引行没删干净（这里绕过改类型的清理直接改列），也查不出彩排的话。"""
    clock = FakeClock()
    session_id = await _session(store, clock)
    await _say(store, clock, session_id, "彩排里试着说芝麻")
    await store.events.insert_game_event(
        live_session_id=session_id, game="minecraft", event_type="milestone", message="芝麻的窝搭好了"
    )
    await _index(store, clock)
    search = MemorySearch(repo=store.memory_index, embedding=None)
    assert len(await search.search("芝麻", sources=("speech", "event"))) == 2

    await store.execute("UPDATE live_sessions SET kind = 'rehearsal' WHERE id = ?", (session_id,))

    assert await search.search("芝麻", sources=("speech", "event")) == []


async def test_lone_cjk_char_next_to_latin_is_found_through_the_substring_path(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    session_id = await _session(store, clock)
    target = await _say(store, clock, session_id, "继续炸tnt了")
    await _index(store, clock)

    (hit,) = await MemorySearch(repo=store.memory_index, embedding=None).search("炸TNT", sources=("speech",))

    assert hit.source_id == target and hit.like_rank == 1


async def test_vector_path_finds_paraphrases_and_hits_on_both_paths_rank_first(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    session_id = await _session(store, clock)
    paraphrase = await _say(store, clock, session_id, "芝麻是只黑猫")
    both = await _say(store, clock, session_id, "小猫咪趴在窝里")
    await _say(store, clock, session_id, "家门口挖了水渠")
    server = FakeEmbeddingServer()
    embedding = server.client()
    await _index(store, clock, embedding)
    search = MemorySearch(repo=store.memory_index, embedding=embedding)

    result = await search.search_detailed("小猫咪", sources=("speech",), wait_vector_ms=5_000)
    hits = result.hits

    assert result.vector_used is True
    assert hits[0].source_id == both and hits[0].keyword_rank == 1 and hits[0].vector_rank is not None
    by_id = {hit.source_id: hit for hit in hits}
    assert by_id[paraphrase].keyword_rank is None and by_id[paraphrase].vector_score == pytest.approx(1.0)


async def test_without_waiting_the_first_batch_is_keyword_only_and_the_vector_is_ready_next_time(
    store: SQLiteDatabase,
) -> None:
    clock = FakeClock()
    session_id = await _session(store, clock)
    paraphrase = await _say(store, clock, session_id, "芝麻是只黑猫")
    server = FakeEmbeddingServer()
    embedding = server.client()
    await _index(store, clock, embedding)
    search = MemorySearch(repo=store.memory_index, embedding=embedding)

    search.prefetch("小猫咪")
    assert await search.search("小猫咪", sources=("speech",)) == [], "不等：这一批只走关键词，没有共同的词"
    # 预取的查询向量算好之后，下一批不等也用得上
    hits = await search.search("小猫咪", sources=("speech",), wait_vector_ms=5_000)
    assert [hit.source_id for hit in hits] == [paraphrase]
    assert len([batch for batch in server.requests if batch == ["小猫咪"]]) == 1, "查询向量算一次就缓存"


async def test_embedding_failure_falls_back_to_keywords(store: SQLiteDatabase) -> None:
    clock = FakeClock()
    session_id = await _session(store, clock)
    target = await _say(store, clock, session_id, "小猫咪趴在窝里")
    await _index(store, clock)
    server = FakeEmbeddingServer()
    server.fail = True
    search = MemorySearch(repo=store.memory_index, embedding=server.client())

    result = await search.search_detailed("小猫咪", sources=("speech",), wait_vector_ms=5_000)

    assert [(hit.source_id, hit.vector_rank) for hit in result.hits] == [(target, None)]
    assert result.vector_used is False, "嵌入失败这一次只走了关键词，调用方要能看出来"


async def test_min_cosine_drops_low_similarity_vectors_from_rrf_fusion(store: SQLiteDatabase) -> None:
    """低于 min_cosine 底线的低相关向量直接丢弃，防止全库最高余弦仅 0.25 时仍靠语义排名塞满候选。"""
    clock = FakeClock()
    session_id = await _session(store, clock)
    # 与"猫"方向余弦 1.0 的强相关行 vs 正交后微偏（余弦约 0.25）的弱噪声行
    strong_id = await store.archive.insert_line(
        live_session_id=session_id, text="黑猫窝搭好了", quote="就叫芝麻", evidence_chat_id=1
    )
    weak_id = await store.archive.insert_line(
        live_session_id=session_id, text="完全无关的种植记录", quote="收了一筐小麦", evidence_chat_id=2
    )
    docs = await store.memory_index.pending_full_text(source="archive", now_ms=clock.now_ms(), limit=10)
    await store.memory_index.write_full_text(source="archive", items=[(doc, doc.document) for doc in docs])
    doc_by_id = {doc.source_id: doc for doc in docs}
    server = FakeEmbeddingServer()
    embedding = server.client()
    await store.memory_index.write_vectors(
        source="archive",
        model_key=embedding.model_key,
        items=[
            (doc_by_id[strong_id], [1.0, 0.0, 0.0]),
            (doc_by_id[weak_id], [0.25, 0.0, 0.9682458]),
        ],
    )
    search = MemorySearch(repo=store.memory_index, embedding=embedding)

    # 默认 min_cosine=0.0 时两条都能看见（供被动想起留痕校准）
    unfiltered = await search.search("小猫", sources=("archive",), wait_vector_ms=5_000)
    assert {hit.source_id for hit in unfiltered} == {strong_id, weak_id}

    # 回想传入 min_cosine=0.50 时，余弦 0.25 的弱向量被直接拦下
    filtered = await search.search("小猫", sources=("archive",), wait_vector_ms=5_000, min_cosine=0.50)
    assert [hit.source_id for hit in filtered] == [strong_id]


async def test_recency_bias_prefers_newer_status_in_same_relevance_band_without_beating_unique_early_fact(
    store: SQLiteDatabase,
) -> None:
    """同分档近场偏置：相关度接近的状态更新优先排较新场次，但绝不跨分档反超高相关的早期唯一事实。"""
    clock = FakeClock()
    early_session = await _session(store, clock)
    clock.tick(86_400_000)
    late_session = await _session(store, clock)

    # 状态更新题：第 1 场住山洞基地 vs 第 2 场搬进山顶基地，关键词覆盖度同档，最新说法应排第 1
    old_home = await store.archive.insert_line(
        live_session_id=early_session, text="基地搬迁记录", quote="我们现在住在山洞基地里", evidence_chat_id=1
    )
    new_home = await store.archive.insert_line(
        live_session_id=late_session, text="基地搬迁记录", quote="我们现在搬进山顶基地里", evidence_chat_id=2
    )
    # 早期唯一事实题：第 1 场唯一提到了紫水晶望远镜（双词全中），第 2 场只擦到普通单字词
    early_unique = await store.archive.insert_line(
        live_session_id=early_session, text="紫水晶望远镜做好了", quote="合成了紫水晶望远镜", evidence_chat_id=3
    )
    late_partial = await store.archive.insert_line(
        live_session_id=late_session, text="普通望远镜架子", quote="摆了个架子", evidence_chat_id=4
    )
    clock.tick(60_000)
    await _index(store, clock)
    search = MemorySearch(repo=store.memory_index, embedding=None)

    status_hits = await search.search("现在 基地", sources=("archive",))
    assert [hit.source_id for hit in status_hits[:2]] == [new_home, old_home]

    unique_hits = await search.search("紫水晶 望远镜", sources=("archive",))
    assert [hit.source_id for hit in unique_hits[:2]] == [early_unique, late_partial]
