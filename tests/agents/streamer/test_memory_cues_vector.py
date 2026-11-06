"""被动想起的向量一路：弹幕一到预取查询向量、意思相近才推、游戏情境变了才查、发出去的只有正文。

假嵌入按字落方向：含"猫"的落在同一个方向（余弦 1），不含"猫""水"的落在第三个方向（与猫类无关）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest

from src.agents.streamer.memory.cues import MemoryCueProvider
from src.agents.streamer.memory.index import MemoryIndexer
from src.agents.streamer.memory.search import MemorySearch
from src.modules.llm.embedding import BACKOFF_BASE_MS
from src.modules.events.payloads.room import RoomMessageUser
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.privacy import PrivacyService
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation, NearbyPlace
from tests.helpers import FakeClock
from tests.modules.llm.fakes import FakeEmbeddingServer
from tests.modules.storage.helpers import make_room_message

_SAVES = {"minecraft": GameSave(id="b", name="新档")}


@pytest.fixture()
async def store(tmp_path: Path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "cues_vector.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture()
async def privacy(store: SQLiteDatabase, tmp_path: Path) -> AsyncIterator[PrivacyService]:
    service = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "forget.key", bot_name="麦麦")
    await service.initialize()
    yield service
    await service.close()


class _Memory:
    """一套能做语义查询的记忆：旧场次里一行猫的档案、一行水车的档案、一条猫的承诺，已建好索引。"""

    def __init__(self, store: SQLiteDatabase) -> None:
        self.store = store
        self.server = FakeEmbeddingServer()
        self.clock = FakeClock()
        # 和生产装配一样：索引补算与检索共用一个嵌入客户端（也就共用一套退避）
        self.client = self.server.client(clock=self.clock.now_ms)
        self.search = MemorySearch(repo=store.memory_index, embedding=self.client)
        self.cat_line = 0
        self.water_line = 0
        self.cat_promise = 0

    async def seed(self) -> "_Memory":
        old = await self.store.sessions.insert_live_session(started_at_ms=self.clock.now_ms() - 86_400_000)
        await self.store.sessions.close_live_session(live_session_id=old, ended_at_ms=self.clock.now_ms() - 80_000_000)
        self.cat_line = await self.store.archive.insert_line(
            live_session_id=old, text="Minecraft：猫取名芝麻", quote="就叫芝麻", evidence_chat_id=1
        )
        self.water_line = await self.store.archive.insert_line(
            live_session_id=old, text="Minecraft：水车搭好了", quote="水车转起来啦", evidence_chat_id=2
        )
        promise = await self.store.commitments.insert_commitment(what="给猫做个窝", quote="下次给猫做个窝")
        assert promise is not None
        self.cat_promise = promise
        await self.indexer().run_once()
        self.server.requests.clear()
        return self

    def indexer(self) -> MemoryIndexer:
        return MemoryIndexer(
            repo=self.store.memory_index, embedding=self.client, embedding_note="", clock=self.clock.now_ms
        )

    async def provider(self, session_id: int, *, privacy: Optional[PrivacyService] = None, **extra: Any):
        return MemoryCueProvider(
            commitments_repo=self.store.commitments,
            chat_repo=self.store.chat,
            viewer_repo=self.store.viewers,
            privacy_service=privacy,
            current_session_provider=lambda: session_id,
            clock=self.clock.now_ms,
            archive_repo=self.store.archive,
            cue_log_repo=self.store.memory_cue_log,
            search=self.search,
            **extra,
        )

    async def warm(self, text: str) -> None:
        """等这段查询文本的向量算好（预取已发出时就是等那一次请求）。"""
        await self.search.search_detailed(text, wait_vector_ms=5_000)

    def sent(self) -> list[str]:
        return [text for batch in self.server.requests for text in batch]


def _danmaku(content: str, *, external_id: str = "u1", message_id: str = "m1", simulated: bool = False) -> Any:
    return make_room_message(
        user=RoomMessageUser(id=external_id, name="阿杰"), content=content, message_id=message_id, simulated=simulated
    )


async def test_danmaku_meaning_pushes_close_lines_once_its_vector_is_ready(store: SQLiteDatabase) -> None:
    """弹幕到达时预取它的向量；算好以后意思相近的档案行与公开承诺推成卡，不相干的不推。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    provider = await memory.provider(session_id)
    message = _danmaku("那只小猫咪最近好吗", message_id="m5")

    await provider.prefetch(message)
    await memory.warm("那只小猫咪最近好吗")
    assert memory.sent() == ["那只小猫咪最近好吗"], "预取只发弹幕正文，算过一次就缓存"

    cues = await provider.collect(batch=[message])

    assert cues.archive_ids == (memory.cat_line,) and cues.commitment_ids == (memory.cat_promise,)
    assert "想起它是因为弹幕 m5 说的像这件事" in cues.text and "下次给猫做个窝" in cues.text
    assert "截至上一场还没做" in cues.text, "做没做是下播整理判的，不替她断言眼下没做"
    assert "水车" not in cues.text
    entries = await store.memory_cue_log.list_session_entries(live_session_id=session_id)
    assert {entry.card_kind for entry in entries} == {"vector"}
    assert all(entry.sources == ["danmaku"] and entry.detail["cosine"] == 1.0 for entry in entries)


async def test_vector_not_ready_means_this_batch_goes_without(store: SQLiteDatabase) -> None:
    """这一批组装时向量还没算好：这一批不推向量卡，也不等它。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    provider = await memory.provider(session_id)
    memory.server.fail = True

    cues = await provider.collect(batch=[_danmaku("那只小猫咪最近好吗")])

    assert cues.archive_ids == () and cues.commitment_ids == ()
    assert memory.server.requests == [], "没预取过的弹幕，组装时也不补发"


async def test_quiet_viewers_commands_short_and_simulated_text_never_leave(
    store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    """发过"忘了我"的观众、遗忘命令本身、太短的弹幕、模拟消息：预取与组装都不发给嵌入服务。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    quiet = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u9",
        user_name="小林",
        timestamp_ms=memory.clock.now_ms(),
        name_match_window_ms=0,
    )
    await store.execute("UPDATE viewers SET forget_requested_at_ms=? WHERE id=?", (1, quiet.viewer_id))
    provider = await memory.provider(session_id, privacy=privacy)
    batch = [
        _danmaku("我的小猫咪丢了好难过", external_id="u9"),
        _danmaku("忘了我"),
        _danmaku("猫猫"),
        _danmaku("模拟观众说小猫咪", simulated=True),
    ]

    for message in batch:
        await provider.prefetch(message)
    cues = await provider.collect(batch=batch)

    assert memory.sent() == []
    assert cues.archive_ids == () and cues.commitment_ids == (), "遗忘命令只出回执行，不推卡"


async def test_game_situation_is_queried_once_per_change(store: SQLiteDatabase) -> None:
    """游戏情境（存档、走到跟前的地点、手上的委派）变了才查一次；同一个情境不反复推。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    provider = await memory.provider(session_id)
    near_den = {"minecraft": GameSituation(places=(NearbyPlace(name="猫窝", distance=6),))}
    expected = "minecraft 存档「新档」 在「猫窝」附近"

    first = await provider.collect(batch=[], current_saves=_SAVES, situations=near_den)
    assert first.archive_ids == (), "这一批向量还没算好"
    await memory.warm(expected)
    assert memory.sent() == [expected], "情境查询只写游戏、存档、地点和委派"

    second = await provider.collect(batch=[], current_saves=_SAVES, situations=near_den)
    assert second.archive_ids == (memory.cat_line,) and "眼下游戏里的情境像这件事" in second.text
    third = await provider.collect(batch=[], current_saves=_SAVES, situations=near_den)
    assert third.archive_ids == () and memory.sent() == [expected], "情境没变不再查"

    await provider.collect(
        batch=[], current_saves=_SAVES, situations=near_den, game_tasks={"minecraft": "去给猫砍点树"}
    )
    assert memory.sent()[-1] == "minecraft 存档「新档」 在「猫窝」附近 正在做：去给猫砍点树"


@pytest.mark.parametrize(
    "change",
    ["viewer", "closed", "brief", "vector_off"],
)
async def test_vector_skips_what_it_must_not_push(store: SQLiteDatabase, change: str) -> None:
    """对某位观众的承诺、已了结的承诺、简报里列着的行、关掉向量一路：都不推。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    extra: dict[str, Any] = {}
    brief: tuple[int, ...] = ()
    if change == "viewer":
        await store.execute("UPDATE commitments SET viewer_id=9 WHERE id=?", (memory.cat_promise,))
    elif change == "closed":
        assert await store.commitments.close_commitment(commitment_id=memory.cat_promise, close_quote="窝做好了")
    elif change == "brief":
        brief = (memory.cat_line,)
    else:
        extra["vector_enabled"] = False
    provider = await memory.provider(session_id, **extra)
    await memory.warm("那只小猫咪最近好吗")

    cues = await provider.collect(batch=[_danmaku("那只小猫咪最近好吗")], brief_archive_ids=brief)

    if change in ("viewer", "closed"):
        assert cues.commitment_ids == () and cues.archive_ids == (memory.cat_line,)
    elif change == "brief":
        assert cues.archive_ids == () and cues.commitment_ids == (memory.cat_promise,)
    else:
        assert cues.text == ""


@pytest.mark.parametrize("who", ["deleted", "never_carded"])
async def test_forget_listed_viewers_without_a_row_never_leave(
    store: SQLiteDatabase, privacy: PrivacyService, who: str
) -> None:
    """不记录名单上、观众行已经没了的人：删除执行过的、没建过档就发"忘了我"的。

    ta 的弹幕预取不发给嵌入服务，组装本批时规则与语义两路都不拿来查，不推卡。
    """
    memory = await _Memory(store).seed()
    await store.execute("UPDATE archive_lines SET keywords=? WHERE id=?", ('["芝麻"]', memory.cat_line))
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    if who == "deleted":
        # 建过档的观众，删除执行时外部 ID 进了不记录名单，观众行没了
        resolved = await store.viewers.resolve_viewer(
            platform="bilibili",
            id_kind="account",
            external_id="u9",
            user_name="小林",
            timestamp_ms=memory.clock.now_ms(),
            name_match_window_ms=0,
        )
        assert await privacy.delete_viewer(viewer_id=resolved.viewer_id, add_to_forget_list=True)
    else:
        # 第一次来就发"忘了我"：没有观众行，只有不记录名单
        disposition = await privacy.process_incoming_viewer_message(
            platform="bilibili", id_kind="account", external_id="u9", message_type="danmaku", content="忘了我吧"
        )
        assert disposition == "forget_command"
    assert await store.viewers.find_viewer_id(platform="bilibili", external_id="u9") is None
    provider = await memory.provider(session_id, privacy=privacy)
    message = _danmaku("芝麻那只小猫咪最近好吗", external_id="u9")

    await provider.prefetch(message)
    cues = await provider.collect(batch=[message])

    assert memory.sent() == [], "不发给嵌入服务"
    assert cues.archive_ids == () and cues.commitment_ids == (), "规则与语义两路都不推"


async def test_embedding_outage_sends_one_request_and_pauses_indexing_too(store: SQLiteDatabase) -> None:
    """嵌入服务回 503：十条弹幕的预取加组装只发出第一条请求，之后退避；索引补算也跟着停，期满再恢复。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    provider = await memory.provider(session_id)
    memory.server.fail = True
    batch = [_danmaku(f"第{index}只小猫咪最近好吗", message_id=f"m{index}") for index in range(10)]

    for message in batch:
        await provider.prefetch(message)
        await memory.warm(message.content)
    cues = await provider.collect(batch=batch)

    assert len(memory.server.requests) == 1, "第一条失败后退避，后面九条的预取与组装都不再发"
    assert cues.archive_ids == () and cues.commitment_ids == ()

    # 索引补算共用这套退避：新承诺这一轮只进全文，不请求向量
    await store.commitments.insert_commitment(what="给狗搭个窝", quote="下次给狗搭个窝")
    await memory.indexer().run_once()
    assert len(memory.server.requests) == 1

    memory.server.fail = False
    memory.clock.tick(BACKOFF_BASE_MS)
    await memory.indexer().run_once()
    assert memory.server.requests[-1] == ["给狗搭个窝\n下次给狗搭个窝"], "退避期满，索引照常补上"


async def test_situation_query_replaces_known_player_names(store: SQLiteDatabase) -> None:
    """委派原话里已知的玩家名（身边的玩家、发过弹幕的观众、游戏里说过话的人）发出去前换成"玩家"；地点名照发。"""
    memory = await _Memory(store).seed()
    session_id = await store.sessions.insert_live_session(started_at_ms=memory.clock.now_ms())
    provider = await memory.provider(session_id)
    provider.note_name("Alex")
    await provider.collect(batch=[_danmaku("晚上好")])
    situation = {
        "minecraft": GameSituation(places=(NearbyPlace(name="猫窝", distance=6),), players=("Steve",)),
    }

    await provider.collect(
        batch=[],
        current_saves=_SAVES,
        situations=situation,
        game_tasks={"minecraft": "帮阿杰、Steve 和 Alex 去猫窝砍树"},
    )
    expected = "minecraft 存档「新档」 在「猫窝」附近 正在做：帮玩家、玩家 和 玩家 去猫窝砍树"
    await memory.warm(expected)

    assert memory.sent() == [expected]
