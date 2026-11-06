"""回想工具的仓储检索面：live_chat 关键词检索、场次边界与观众昵称全表。

覆盖：多关键词取交集、场次过滤、newest / oldest 两向取批、只有已播出的台词才命中
（dropped_at_ms 带标记的不算说过的话）、LIKE 元字符按字面匹配、观众消息只按场次取、
场次边界按场内最后一条明细排序、已知昵称覆盖当前名与曾用名并剔掉占位名。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator, Optional

import pytest

from src.modules.storage.database import SQLiteDatabase
from tests.modules.storage.viewer_seed import seed_viewer


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="memory-recall-queries-"))
    yield td / "recall.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


async def _session(store: SQLiteDatabase, *, started: int, ended: Optional[int] = None) -> int:
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=started)
    if ended is not None:
        await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


async def _say(
    store: SQLiteDatabase,
    pk: int,
    text: str,
    ts: int,
    *,
    dropped_at_ms: Optional[int] = None,
) -> int:
    return await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=ts,
        sender_role="assistant",
        sender_id="麦麦",
        sender_name="麦麦",
        content=text,
        message_type="speak",
        dropped_at_ms=dropped_at_ms,
    )


async def _danmaku(store: SQLiteDatabase, pk: int, *, user_id: str, name: str, text: str, ts: int) -> int:
    viewer_id = await seed_viewer(store.viewers, platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts)
    return await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=ts,
        sender_role="viewer",
        sender_id=user_id,
        sender_name=name,
        content=text,
        message_type="danmaku",
        platform="bilibili",
        message_id=f"{user_id}-{ts}",
        viewer_id=viewer_id,
        id_kind="account",
    )


async def test_list_speech_by_ids_keeps_the_given_order_and_only_aired_lines(store: SQLiteDatabase) -> None:
    """回想按检索结果的先后还原台词；没送到观众面前的（dropped_at_ms 非空）不算说过。"""
    pk = await _session(store, started=1_000)
    first = await _say(store, pk, "今天挖矿挖到钻石了", 1_100)
    dropped = await _say(store, pk, "这句没送到观众面前", 1_200, dropped_at_ms=1_250)
    second = await _say(store, pk, "钻石真好看", 1_300)
    viewer = await _danmaku(store, pk, user_id="u1", name="老王", text="观众的弹幕", ts=1_400)

    rows = await store.chat.list_speech_by_ids(chat_ids=[second, dropped, first, viewer, 999])

    assert [row.id for row in rows] == [second, first]


async def test_list_archive_lines_by_ids_keeps_the_given_order(store: SQLiteDatabase) -> None:
    pk = await _session(store, started=1_000)
    chat_id = await _say(store, pk, "从今天起你就叫团子", 1_100)
    first = await store.archive.insert_line(
        live_session_id=pk, text="取名团子", quote="就叫团子", evidence_chat_id=chat_id
    )
    second = await store.archive.insert_line(
        live_session_id=pk, text="挖了水渠", quote="挖了水渠", evidence_chat_id=chat_id
    )

    lines = await store.archive.list_lines_by_ids(line_ids=[second, 999, first])

    assert [line.id for line in lines] == [second, first]


async def test_search_recall_danmaku_treats_like_wildcards_literally(store: SQLiteDatabase) -> None:
    """关键词里的 % 与 _ 按字面匹配，不变成通配符。"""
    pk = await _session(store, started=1_000)
    await _danmaku(store, pk, user_id="u1", name="老王", text="进度 100% 了", ts=1_100)
    await _danmaku(store, pk, user_id="u1", name="老王", text="进度 1000 了", ts=1_200)
    rows = await store.chat.search_recall_danmaku(keywords=["100%"], live_session_id=pk)
    assert [row.content for row in rows] == ["进度 100% 了"]

    await _danmaku(store, pk, user_id="u1", name="老王", text="文件叫 a_b", ts=1_300)
    await _danmaku(store, pk, user_id="u1", name="老王", text="文件叫 axb", ts=1_400)
    rows = await store.chat.search_recall_danmaku(keywords=["a_b"], live_session_id=pk)
    assert [row.content for row in rows] == ["文件叫 a_b"]


async def test_search_recall_danmaku_only_viewer_messages_of_the_session(store: SQLiteDatabase) -> None:
    first = await _session(store, started=1_000, ended=2_000)
    second = await _session(store, started=3_000)
    await _danmaku(store, first, user_id="u1", name="老王", text="上一场说了挖矿", ts=1_100)
    await _danmaku(store, second, user_id="u1", name="老王", text="本场说挖矿", ts=3_100)
    await _say(store, second, "我也说挖矿", 3_200)

    rows = await store.chat.search_recall_danmaku(keywords=["挖矿"], live_session_id=second)
    assert [row.content for row in rows] == ["本场说挖矿"]


async def test_list_chat_session_bounds_orders_by_last_message(store: SQLiteDatabase) -> None:
    first = await _session(store, started=1_000, ended=2_000)
    second = await _session(store, started=3_000, ended=4_000)
    await _say(store, first, "第一场", 1_100)
    await _say(store, first, "第一场收尾", 1_500)
    await _say(store, second, "第二场", 3_100)

    assert await store.chat.list_chat_session_bounds() == [(second, 3_100), (first, 1_500)]


async def test_list_chat_session_bounds_empty(store: SQLiteDatabase) -> None:
    assert await store.chat.list_chat_session_bounds() == []


async def test_list_known_names_covers_current_and_former_names(store: SQLiteDatabase) -> None:
    await seed_viewer(store.viewers, platform="bilibili", user_id="u1", user_name="老王", timestamp_ms=1_000)
    # 同一个账号改名：曾用名与当前名都要能取到，且不重复
    await seed_viewer(store.viewers, platform="bilibili", user_id="u1", user_name="王老", timestamp_ms=2_000)
    # 采集器拿不到昵称时的占位名与打码昵称不代表任何人，不返回
    await seed_viewer(store.viewers, platform="bilibili", user_id="u2", user_name="未知用户", timestamp_ms=3_000)
    await seed_viewer(store.viewers, platform="bilibili", user_id="u3", user_name="用***", timestamp_ms=4_000)

    names = await store.viewers.list_known_names()
    assert "老王" in names
    assert "王老" in names
    assert names.count("老王") == 1
    assert "未知用户" not in names
    assert "用***" not in names


async def test_list_known_names_empty(store: SQLiteDatabase) -> None:
    assert await store.viewers.list_known_names() == []
