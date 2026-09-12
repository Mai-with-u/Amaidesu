"""
话题摘要落库链路单测（timeline_summary + topics）

覆盖：
- _persist_topic_snapshot：timeline_summary 一行一段摘要历史，窗口为 [上次摘要, 本次]
- topics 快照投影：本场旧行被清除后插入最新关键词行 + 摘要句行
- 场次主键经 LiveSessionManager 解析（跨表可 JOIN）
- 未注入 sqlite_store 时整体跳过，不报错
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-topic-persist-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


class _FakeSessionManager:
    def __init__(self, pk: int) -> None:
        self._pk = pk

    async def resolve_pk(self) -> int:
        return self._pk


def _make_maintainer(store: SQLiteStore, pk: int = 777) -> BackgroundMaintainer:
    room_state = RoomState()
    room_state.set_topic_summary("占位", now_ms=1)
    return BackgroundMaintainer(
        {"summary_interval_ms": 60_000},
        room_state=room_state,
        live_session_store=store,
        session_manager=_FakeSessionManager(pk),
        sqlite_store=store,
        session_id="live",
    )


@pytest.mark.asyncio
async def test_persist_writes_timeline_and_topics(store: SQLiteStore) -> None:
    maintainer = _make_maintainer(store, pk=777)
    await maintainer._persist_topic_snapshot(
        "观众在讨论新版本更新", now_ms=120_000, previous_summary_ms=60_000
    )

    timeline = await store.execute("SELECT * FROM timeline_summary")
    assert len(timeline) == 1
    assert timeline[0]["live_session_id"] == 777
    assert timeline[0]["start_ms"] == 60_000
    assert timeline[0]["end_ms"] == 120_000
    assert timeline[0]["summary"] == "观众在讨论新版本更新"

    topics = await store.execute("SELECT * FROM topics ORDER BY source, label")
    # 无关键词时只有摘要句行
    assert [t["source"] for t in topics] == ["summary"]
    assert topics[0]["label"] == "观众在讨论新版本更新"
    assert topics[0]["score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_persist_refreshes_topics_snapshot(store: SQLiteStore) -> None:
    maintainer = _make_maintainer(store)
    await maintainer._persist_topic_snapshot("第一轮摘要", now_ms=60_000, previous_summary_ms=0)
    await maintainer._persist_topic_snapshot("第二轮摘要", now_ms=120_000, previous_summary_ms=60_000)

    # topics 是快照投影：第二轮后旧行（含第一轮摘要句）被清除，仅剩最新一轮
    rows = await store.execute("SELECT label FROM topics")
    assert len(rows) == 1
    assert rows[0]["label"] == "第二轮摘要"

    # 摘要历史追加不覆盖
    timeline = await store.execute("SELECT summary FROM timeline_summary ORDER BY id")
    assert [t["summary"] for t in timeline] == ["第一轮摘要", "第二轮摘要"]

    # 首次窗口起点回退一个摘要间隔（previous=0 → now - interval）
    first = await store.execute("SELECT start_ms, end_ms FROM timeline_summary ORDER BY id LIMIT 1")
    assert first[0]["start_ms"] == 0
    assert first[0]["end_ms"] == 60_000


@pytest.mark.asyncio
async def test_persist_skipped_without_store() -> None:
    maintainer = BackgroundMaintainer(
        {},
        room_state=RoomState(),
        sqlite_store=None,
        session_id="live",
    )
    # 不注入 sqlite_store：整体跳过，不抛异常
    await maintainer._persist_topic_snapshot("摘要", now_ms=1_000, previous_summary_ms=0)
