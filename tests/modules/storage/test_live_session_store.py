"""
live_sessions 场次状态写链单测

覆盖：
- 心跳首次调用即开行（started_at_ms 取自首次 updated_at_ms，stream_id 落原始 session_id）
- 重复心跳仍一行：热度/计数/updated_at_ms 更新，started_at_ms / platform 保持首次值
- session_id → 主键映射与 StorageLedger 明细写入共用同一算法（外键不悬空）
- end_live_session：命中开行场次写 ended_at_ms 返回 True；未开行返回 False
- BackgroundMaintainer 轻 tick 真正把心跳写进表（此前因方法缺失被 hasattr 静默跳过）
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.modules.storage.sqlite_store import SQLiteStore, session_id_to_pk
from src.modules.storage.storage_ledger import StorageLedger


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-live-session-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# ===== session_id_to_pk =====


def test_session_id_to_pk_is_stable_and_shared_with_ledger() -> None:
    pk_a = session_id_to_pk("live")
    pk_b = session_id_to_pk("live")
    assert pk_a == pk_b
    assert 0 <= pk_a < 2**32
    # StorageLedger 的旧接口必须与权威函数同值，否则明细行与场次行主键错位
    assert StorageLedger._session_pk_to_int("live") == pk_a
    assert session_id_to_pk("other") != pk_a


# ===== update_live_session_heartbeat =====


@pytest.mark.asyncio
async def test_first_heartbeat_opens_session_row(store: SQLiteStore) -> None:
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=2,
        viewer_count=10,
        audience_total=100,
        updated_at_ms=1_700_000_000_000,
        platform="bilibili",
    )
    row = await store.get_live_session(session_id="live")
    assert row is not None
    assert row["id"] == session_id_to_pk("live")
    assert row["stream_id"] == "live"
    assert row["platform"] == "bilibili"
    assert row["started_at_ms"] == 1_700_000_000_000
    assert row["updated_at_ms"] == 1_700_000_000_000
    assert row["ended_at_ms"] is None
    assert row["heat"] == 2
    assert row["viewer_count"] == 10
    assert row["audience_total"] == 100


@pytest.mark.asyncio
async def test_repeated_heartbeat_updates_in_place(store: SQLiteStore) -> None:
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=1,
        viewer_count=0,
        audience_total=0,
        updated_at_ms=1_000,
        platform="bilibili",
    )
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=3,
        viewer_count=42,
        audience_total=500,
        updated_at_ms=6_000,
        platform="should-not-overwrite",
    )
    rows = await store.execute("SELECT * FROM live_sessions")
    assert len(rows) == 1
    row = rows[0]
    assert row["heat"] == 3
    assert row["viewer_count"] == 42
    assert row["audience_total"] == 500
    assert row["updated_at_ms"] == 6_000
    # 开行时刻与平台由首次心跳决定，后续心跳不覆盖
    assert row["started_at_ms"] == 1_000
    assert row["platform"] == "bilibili"


@pytest.mark.asyncio
async def test_heartbeat_platform_defaults_to_unknown(store: SQLiteStore) -> None:
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=1,
        viewer_count=0,
        audience_total=0,
        updated_at_ms=1_000,
    )
    row = await store.get_live_session(session_id="live")
    assert row is not None
    assert row["platform"] == "unknown"


@pytest.mark.asyncio
async def test_heartbeat_pk_matches_ledger_detail_rows(store: SQLiteStore) -> None:
    """场次行主键与 StorageLedger 写明细用的 live_session_id 必须一致。"""
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=1,
        viewer_count=0,
        audience_total=0,
        updated_at_ms=1_000,
    )
    await store.insert_live_chat(
        live_session_id=StorageLedger._session_pk_to_int("live"),
        timestamp_ms=1_500,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    rows = await store.execute(
        "SELECT c.content FROM live_chat c JOIN live_sessions s ON c.live_session_id = s.id WHERE s.stream_id = ?",
        ("live",),
    )
    assert [r["content"] for r in rows] == ["hi"]


# ===== end_live_session =====


@pytest.mark.asyncio
async def test_end_live_session_writes_ended_at(store: SQLiteStore) -> None:
    await store.update_live_session_heartbeat(
        session_id="live",
        heat=1,
        viewer_count=0,
        audience_total=0,
        updated_at_ms=1_000,
    )
    hit = await store.end_live_session(session_id="live", ended_at_ms=9_000)
    assert hit is True
    row = await store.get_live_session(session_id="live")
    assert row is not None
    assert row["ended_at_ms"] == 9_000
    assert row["updated_at_ms"] == 9_000


@pytest.mark.asyncio
async def test_end_live_session_without_row_returns_false(store: SQLiteStore) -> None:
    hit = await store.end_live_session(session_id="never-started", ended_at_ms=9_000)
    assert hit is False
    assert await store.get_live_session(session_id="never-started") is None


# ===== BackgroundMaintainer 轻 tick → live_sessions =====


@pytest.mark.asyncio
async def test_background_light_tick_persists_heartbeat(store: SQLiteStore) -> None:
    room_state = RoomState()
    maintainer = BackgroundMaintainer(
        {},
        room_state=room_state,
        live_session_store=store,
        session_id="live",
    )
    await maintainer._light_tick(now_ms=5_000)
    await maintainer._light_tick(now_ms=10_000)

    rows = await store.execute("SELECT * FROM live_sessions")
    assert len(rows) == 1
    assert rows[0]["stream_id"] == "live"
    assert rows[0]["started_at_ms"] == 5_000
    assert rows[0]["updated_at_ms"] == 10_000
    # 冷场默认热度 low → 1
    assert rows[0]["heat"] == 1
