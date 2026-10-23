"""整场记录：从库里读出一场直播的对话并整理成推给适配器插件的格式。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.maibot_sync import StorageTranscriptSource
from src.modules.storage.database import SQLiteDatabase

BASE_MS = 1_792_760_700_000


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="maibot-transcript-"))
    yield td / "transcript.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


async def _ended_session(store: SQLiteDatabase, *, source: str = "manual", platform: str = "bilibili") -> int:
    pk = await store.sessions.insert_live_session(
        stream_id="778899", platform=platform, started_at_ms=BASE_MS, title="周五 MC 建房", source=source
    )
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=BASE_MS + 3_600_000)
    return pk


@pytest.mark.asyncio
async def test_transcript_merges_chat_and_super_chats_in_time_order(store: SQLiteDatabase) -> None:
    pk = await _ended_session(store)
    chat = store.chat
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE_MS + 1_000, sender_role="viewer", content="来了来了",
        message_type="danmaku", sender_id="1001", sender_name="小明", platform="bilibili",
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE_MS + 2_000, sender_role="assistant", content="欢迎小明！",
        message_type="speak", sender_name="主播", platform="bilibili",
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE_MS + 3_000, sender_role="viewer", content="模拟弹幕",
        message_type="danmaku", sender_id="9", sender_name="假人", simulated=True,
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE_MS + 4_000, sender_role="viewer", content="  ",
        message_type="danmaku", sender_id="1002", sender_name="阿花",
    )  # fmt: skip
    await chat.insert_super_chat(
        live_session_id=pk, timestamp_ms=BASE_MS + 1_500, platform="bilibili", user_id="1002", user_name="阿花",
        message="加油", total_price=30_000, currency="bilibili_gold_coin",
    )  # fmt: skip

    body = await StorageTranscriptSource(sessions=store.sessions, chat=store.chat).load(pk)

    assert body is not None
    assert (body["type"], body["live_session_id"], body["platform"], body["room_id"], body["title"]) == (
        "transcript",
        pk,
        "bilibili",
        "778899",
        "周五 MC 建房",
    )
    assert (body["started_at_ms"], body["ended_at_ms"]) == (BASE_MS, BASE_MS + 3_600_000)
    assert body["lines"] == [
        {"ts_ms": BASE_MS + 1_000, "role": "viewer", "user_id": "1001", "user_name": "小明", "text": "来了来了"},
        {"ts_ms": BASE_MS + 1_500, "role": "viewer", "user_id": "1002", "user_name": "阿花", "text": "[醒目留言 ¥30] 加油"},
        {"ts_ms": BASE_MS + 2_000, "role": "streamer", "user_id": "", "user_name": "", "text": "欢迎小明！"},
    ]


@pytest.mark.asyncio
async def test_no_transcript_for_replay_unended_empty_or_unknown_sessions(store: SQLiteDatabase) -> None:
    source = StorageTranscriptSource(sessions=store.sessions, chat=store.chat)
    replay = await _ended_session(store, source="replay")
    await store.chat.insert_live_chat(
        live_session_id=replay, timestamp_ms=BASE_MS, sender_role="viewer", content="回放弹幕", message_type="danmaku"
    )
    ongoing = await store.sessions.insert_live_session(stream_id="778899", platform="bilibili", started_at_ms=BASE_MS)
    await store.chat.insert_live_chat(
        live_session_id=ongoing, timestamp_ms=BASE_MS, sender_role="viewer", content="直播中", message_type="danmaku"
    )
    empty = await _ended_session(store)

    assert await source.load(replay) is None
    assert await source.load(ongoing) is None
    assert await source.load(empty) is None
    assert await source.load(99_999) is None


@pytest.mark.asyncio
async def test_unknown_platform_left_blank_for_plugin_default(store: SQLiteDatabase) -> None:
    pk = await _ended_session(store, platform="unknown")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE_MS, sender_role="assistant", content="自言自语", message_type="speak"
    )

    body = await StorageTranscriptSource(sessions=store.sessions, chat=store.chat).load(pk)

    assert body is not None and body["platform"] == ""
