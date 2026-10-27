"""SessionRecapRepo（场次回顾仓储）单元测试

覆盖：upsert / get / 空文本拒写 / 最近几场倒序与排除本场 / 已结束无回顾的场次清单 / 删除。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="session-recap-repo-"))
    yield td / "recaps.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


async def _session(store: SQLiteDatabase, *, started: int, ended: int | None) -> int:
    pk = await store.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=started)
    if ended is not None:
        await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=ended)
    return pk


@pytest.mark.asyncio
async def test_upsert_get_and_reject_empty(store: SQLiteDatabase) -> None:
    repo = store.recaps
    assert await repo.upsert_recap(live_session_id=7, started_at_ms=1_000, ended_at_ms=2_000, recap_text="  ") is False
    assert await repo.get_recap(live_session_id=7) is None

    assert await repo.upsert_recap(live_session_id=7, started_at_ms=1_000, ended_at_ms=2_000, recap_text="第一版")
    assert await repo.upsert_recap(live_session_id=7, started_at_ms=1_000, ended_at_ms=2_500, recap_text="第二版")
    row = await repo.get_recap(live_session_id=7)
    assert row is not None
    assert row.recap_text == "第二版"
    assert row.ended_at_ms == 2_500


@pytest.mark.asyncio
async def test_list_recent_orders_by_start_and_excludes_current(store: SQLiteDatabase) -> None:
    repo = store.recaps
    for sid, started in ((1, 1_000), (2, 5_000), (3, 9_000)):
        await repo.upsert_recap(
            live_session_id=sid, started_at_ms=started, ended_at_ms=started + 100, recap_text=f"第{sid}场"
        )

    rows = await repo.list_recent_recaps(limit=2)
    assert [r.live_session_id for r in rows] == [3, 2]

    # 本场（id=3）进行中：排除后"上一场"是第 2 场
    rows = await repo.list_recent_recaps(limit=1, exclude_session_id=3)
    assert [r.live_session_id for r in rows] == [2]


@pytest.mark.asyncio
async def test_ended_sessions_without_recap(store: SQLiteDatabase) -> None:
    """已结账且无回顾的场次按结束时刻倒序列出；进行中与已有回顾的不列。"""
    done_old = await _session(store, started=1_000, ended=2_000)
    done_new = await _session(store, started=3_000, ended=4_000)
    await _session(store, started=5_000, ended=None)  # 进行中
    recapped = await _session(store, started=6_000, ended=7_000)
    await store.recaps.upsert_recap(live_session_id=recapped, started_at_ms=6_000, ended_at_ms=7_000, recap_text="有了")

    rows = await store.recaps.list_ended_sessions_without_recap(limit=5)
    assert [r.live_session_id for r in rows] == [done_new, done_old]

    rows = await store.recaps.list_ended_sessions_without_recap(limit=1)
    assert [r.live_session_id for r in rows] == [done_new]


@pytest.mark.asyncio
async def test_delete(store: SQLiteDatabase) -> None:
    repo = store.recaps
    await repo.upsert_recap(live_session_id=9, started_at_ms=1, ended_at_ms=2, recap_text="x")
    assert await repo.delete_recap(live_session_id=9) is True
    assert await repo.delete_recap(live_session_id=9) is False
