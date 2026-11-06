"""混合检索：关键词按 BM25、子串兜底、语义按余弦，RRF 融合；查询时只查正式场；嵌入不可用退回关键词。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import httpx
import pytest
from openai import AsyncOpenAI

from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.search import RRF_K, MemorySearch
from src.modules.config.model_schemas import EmbeddingConfig
from src.modules.llm.embedding import EmbeddingClient
from src.modules.storage.database import SQLiteDatabase
from tests.helpers import FakeClock

# 假嵌入的"语义"：含哪个字就落在哪个方向上，其余落在第三个方向
_AXES = (("猫", [1.0, 0.0, 0.0]), ("水", [0.0, 1.0, 0.0]))


class _FakeEmbeddingServer:
    def __init__(self) -> None:
        self.requests: List[List[str]] = []
        self.fail = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        body: Dict[str, Any] = json.loads(request.content)
        self.requests.append(list(body["input"]))
        if self.fail:
            return httpx.Response(503, json={"error": {"message": "忙"}})
        data = [
            {
                "object": "embedding",
                "index": i,
                "embedding": next((v for ch, v in _AXES if ch in text), [0.0, 0.0, 1.0]),
            }
            for i, text in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"object": "list", "data": data, "model": body["model"]})

    def client(self) -> EmbeddingClient:
        sdk = AsyncOpenAI(
            api_key="sk-test",
            base_url="https://embed.example/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )
        return EmbeddingClient(sdk=sdk, config=EmbeddingConfig(provider="p", model_identifier="toy"))


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
    server = _FakeEmbeddingServer()
    embedding = server.client()
    await _index(store, clock, embedding)
    search = MemorySearch(repo=store.memory_index, embedding=embedding)

    hits = await search.search("小猫咪", sources=("speech",), wait_vector_ms=5_000)

    assert hits[0].source_id == both and hits[0].keyword_rank == 1 and hits[0].vector_rank is not None
    by_id = {hit.source_id: hit for hit in hits}
    assert by_id[paraphrase].keyword_rank is None and by_id[paraphrase].vector_score == pytest.approx(1.0)


async def test_without_waiting_the_first_batch_is_keyword_only_and_the_vector_is_ready_next_time(
    store: SQLiteDatabase,
) -> None:
    clock = FakeClock()
    session_id = await _session(store, clock)
    paraphrase = await _say(store, clock, session_id, "芝麻是只黑猫")
    server = _FakeEmbeddingServer()
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
    server = _FakeEmbeddingServer()
    server.fail = True
    search = MemorySearch(repo=store.memory_index, embedding=server.client())

    hits = await search.search("小猫咪", sources=("speech",), wait_vector_ms=5_000)

    assert [(hit.source_id, hit.vector_rank) for hit in hits] == [(target, None)]
