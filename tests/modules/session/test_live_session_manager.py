"""LiveSessionManager 单测：场次生命周期 / 防膨胀 / 归属解析 / 生命周期事件。"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.session import LiveSessionManager, SCRATCH_STREAM_ID
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="session-manager-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus, None]:
    b = EventBus()
    yield b
    await b.cleanup()


def _make_manager(store: SQLiteStore, bus: EventBus) -> LiveSessionManager:
    return LiveSessionManager(store, bus, platform="bilibili", room_id="room-1")


class _Collector:
    """生命周期事件收集器。"""

    def __init__(self, bus: EventBus) -> None:
        self.started: list[LiveStartedPayload] = []
        self.ended: list[LiveEndedPayload] = []
        bus.on(CoreEvents.LIVE_STARTED, self._on_started, model_class=LiveStartedPayload)
        bus.on(CoreEvents.LIVE_ENDED, self._on_ended, model_class=LiveEndedPayload)

    async def _on_started(self, name: str, payload: LiveStartedPayload, source: str) -> None:
        self.started.append(payload)

    async def _on_ended(self, name: str, payload: LiveEndedPayload, source: str) -> None:
        self.ended.append(payload)


@pytest.mark.asyncio
async def test_start_creates_scratch_and_resolves_to_it(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.resolve_pk()
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["source"] == "scratch"
    assert row["stream_id"] == SCRATCH_STREAM_ID
    # 临时场次是兜底桶，不是一场直播：不发 live.started
    assert collector.started == []
    # 反复解析稳定
    assert await manager.resolve_pk() == pk


@pytest.mark.asyncio
async def test_open_session_emits_started_and_switches_resolution(
    store: SQLiteStore, bus: EventBus
) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(title="晚间场")
    await asyncio.sleep(0.02)  # emit 默认后台分发，等事件落地
    assert collector.started and collector.started[0].live_session_id == pk
    assert collector.started[0].source == "manual"
    assert collector.started[0].room_id == "room-1"
    assert await manager.resolve_pk() == pk

    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["source"] == "manual"
    assert row["ended_at_ms"] is None


@pytest.mark.asyncio
async def test_open_twice_auto_closes_previous(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    first = await manager.open_session()
    second = await manager.open_session()
    await asyncio.sleep(0.02)
    assert first != second
    assert manager.active_pk == second
    # 不变量：至多一个显式进行中场次
    assert len(collector.ended) == 1
    assert collector.ended[0].live_session_id == first
    rows = await store.execute(
        "SELECT * FROM live_sessions WHERE source='manual' AND ended_at_ms IS NULL"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_close_empty_session_discards_row(store: SQLiteStore, bus: EventBus) -> None:
    """空场次不留行：开启后无任何明细就结束 → 整行丢弃，事件带 empty_discarded 标记。"""
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session()
    closed = await manager.close_session()
    assert closed is True
    await asyncio.sleep(0.02)
    assert await store.get_live_session(live_session_id=pk) is None
    assert collector.ended and collector.ended[0].empty_discarded is True
    assert collector.ended[0].duration_ms is not None


@pytest.mark.asyncio
async def test_close_session_with_details_keeps_row(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session()
    await store.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_100,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    closed = await manager.close_session()
    assert closed is True
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["ended_at_ms"] is not None


@pytest.mark.asyncio
async def test_close_without_active_returns_false(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()
    assert await manager.close_session() is False


@pytest.mark.asyncio
async def test_delete_session_cascades_and_scratch_rebuilds(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()

    scratch_pk = await manager.resolve_pk()
    await store.insert_live_chat(
        live_session_id=scratch_pk,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="临时消息",
        message_type="danmaku",
    )
    assert await manager.delete_session(scratch_pk) is True
    rows = await store.execute("SELECT * FROM live_chat")
    assert rows == []

    # 临时场次删除后下次解析自动重建
    rebuilt = await manager.resolve_pk()
    assert rebuilt != scratch_pk


@pytest.mark.asyncio
async def test_delete_active_session_closes_first(store: SQLiteStore, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session()
    await manager.delete_session(pk)
    await asyncio.sleep(0.02)
    assert manager.active_pk is None
    assert collector.ended and collector.ended[0].live_session_id == pk


@pytest.mark.asyncio
async def test_startup_closes_dangling_sessions(store: SQLiteStore, bus: EventBus) -> None:
    """上次进程未正常退出的"进行中"显式场次，启动时以最后活动时刻收口。"""
    dangling = await store.insert_live_session(started_at_ms=1_000, source="manual")
    await store.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )

    manager = _make_manager(store, bus)
    await manager.start()

    row = await store.get_live_session(live_session_id=dangling)
    assert row is not None
    assert row["ended_at_ms"] == 9_000
    assert manager.active_pk is None
