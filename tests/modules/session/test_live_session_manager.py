"""LiveSessionManager 单测：场次生命周期 / 归属解析 / 生命周期事件。

显式开启场次才落库语义：无显式场次期间 ``resolve_pk()`` 返回 ``None``，
不创建任何兜底行——下游落库路径据此跳过。
"""

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
from src.modules.session import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="session-manager-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus, None]:
    b = EventBus()
    yield b
    await b.cleanup()


def _make_manager(store: SQLiteDatabase, bus: EventBus) -> LiveSessionManager:
    return LiveSessionManager(store.sessions, store.chat, bus, platform="bilibili", room_id="room-1")


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
async def test_resolve_pk_returns_none_without_active_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """无显式场次时 ``resolve_pk()`` 返回 ``None``，且不创建任何 live_sessions 行。"""
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    assert await manager.resolve_pk() is None
    assert manager.active_pk is None
    rows = await store.execute("SELECT * FROM live_sessions")
    assert rows == [], "无显式场次期间不应自动建任何兜底行"

    # 不发 live.started（不开场次就没有生命周期事件）
    assert collector.started == []


@pytest.mark.asyncio
async def test_open_session_emits_started_and_switches_resolution(
    store: SQLiteDatabase, bus: EventBus
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

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["source"] == "manual"
    assert row["ended_at_ms"] is None


@pytest.mark.asyncio
async def test_open_twice_auto_closes_previous(store: SQLiteDatabase, bus: EventBus) -> None:
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
async def test_close_empty_session_discards_row(store: SQLiteDatabase, bus: EventBus) -> None:
    """空场次不留行：开启后无任何明细就结束 → 整行丢弃，事件带 empty_discarded 标记。"""
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session()
    closed = await manager.close_session()
    assert closed is True
    await asyncio.sleep(0.02)
    assert await store.sessions.get_live_session(live_session_id=pk) is None
    assert collector.ended and collector.ended[0].empty_discarded is True
    assert collector.ended[0].duration_ms is not None


@pytest.mark.asyncio
async def test_close_session_with_details_keeps_row(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session()
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_100,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    closed = await manager.close_session()
    assert closed is True
    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["ended_at_ms"] is not None


@pytest.mark.asyncio
async def test_close_without_active_returns_false(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()
    assert await manager.close_session() is False


@pytest.mark.asyncio
async def test_delete_active_session_closes_first(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session()
    await manager.delete_session(pk)
    await asyncio.sleep(0.02)
    assert manager.active_pk is None
    assert collector.ended and collector.ended[0].live_session_id == pk


@pytest.mark.asyncio
async def test_delete_session_cascades_detail_rows(store: SQLiteDatabase, bus: EventBus) -> None:
    """删除场次：live_chat 等明细级联清除；行不存在返回 False。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session()
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="弹幕",
        message_type="danmaku",
    )
    assert await manager.delete_session(pk) is True
    rows = await store.execute("SELECT * FROM live_chat")
    assert rows == []

    # 不存在的行返回 False
    assert await manager.delete_session(pk) is False


@pytest.mark.asyncio
async def test_startup_closes_dangling_sessions(store: SQLiteDatabase, bus: EventBus) -> None:
    """上次进程未正常退出的"进行中"显式场次，启动时以最后活动时刻收口。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )

    manager = _make_manager(store, bus)
    await manager.start()

    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None
    assert row["ended_at_ms"] == 9_000
    assert manager.active_pk is None


@pytest.mark.asyncio
async def test_resolve_pk_after_close_returns_none(store: SQLiteDatabase, bus: EventBus) -> None:
    """显式场次结束 → ``resolve_pk()`` 回到 None，无兜底行补建。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session()
    assert await manager.resolve_pk() == pk
    await manager.close_session()
    assert await manager.resolve_pk() is None
    # 唯一在场 live_sessions 行是已关闭的 pk（empty_discarded=False 因为没有明细也没显式 insert）
    rows = await store.execute("SELECT * FROM live_sessions WHERE ended_at_ms IS NULL")
    assert rows == [], "无显式场次期间不应有 ended_at_ms IS NULL 的进行中行"
