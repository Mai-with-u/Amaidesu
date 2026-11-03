"""LiveSessionManager 单测：场次生命周期 / 归属解析 / 生命周期事件。

显式开启场次才落库语义：无显式场次期间 ``resolve_pk()`` 返回 ``None``，
不创建任何兜底行——下游落库路径据此跳过。

归属解析分两问：``resolve_pk()`` 是活跃语义（当下有没有进行中场次），
``resolve_attribution_pk()`` 回答"这条消息该记在哪一场"——场后发言归到
最近一场已结束的场次，一场都没结束过才返回 ``None``。
"""

from __future__ import annotations

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


async def test_open_session_emits_started_and_switches_resolution(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(kind="live", title="晚间场")
    await bus.wait_until_drained()  # emit 默认后台分发，等事件落地
    assert collector.started and collector.started[0].live_session_id == pk
    assert collector.started[0].source == "manual"
    assert collector.started[0].kind == "live"
    assert collector.started[0].room_id == "room-1"
    assert await manager.resolve_pk() == pk
    assert manager.active_kind == "live"

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None
    assert row.source == "manual"
    assert row.kind == "live"
    assert row.ended_at_ms is None


async def test_open_twice_auto_closes_previous(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    first = await manager.open_session(kind="live")
    second = await manager.open_session(kind="live")
    await bus.wait_until_drained()
    assert first != second
    assert manager.active_pk == second
    # 不变量：至多一个显式进行中场次；被顶掉的旧场带 superseded 标记
    assert len(collector.ended) == 1
    assert collector.ended[0].live_session_id == first
    assert collector.ended[0].superseded is True
    rows = await store.execute("SELECT * FROM live_sessions WHERE source='manual' AND ended_at_ms IS NULL")
    assert len(rows) == 1


async def test_rehearsal_kind_rides_both_lifecycle_events(store: SQLiteDatabase, bus: EventBus) -> None:
    """彩排场：类型落库，并随 live.started / live.ended 一起广播；结账后内存态清空。"""
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(kind="rehearsal", title="彩排")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=1_000, sender_role="viewer", content="hi", message_type="danmaku"
    )
    assert manager.active_kind == "rehearsal"
    await manager.close_session()
    await bus.wait_until_drained()

    assert [p.kind for p in collector.started] == ["rehearsal"]
    assert [p.kind for p in collector.ended] == ["rehearsal"]
    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.kind == "rehearsal"
    assert manager.active_kind is None


async def test_set_session_kind_only_corrects_ended_sessions(store: SQLiteDatabase, bus: EventBus) -> None:
    """更正类型：进行中的场次拒绝（开播时已按类型分流），已结账的场次可改。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=1_000, sender_role="viewer", content="hi", message_type="danmaku"
    )
    assert await manager.set_session_kind(pk, "rehearsal") is False
    await manager.close_session()
    assert await manager.set_session_kind(pk, "rehearsal") is True
    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.kind == "rehearsal"
    assert await manager.set_session_kind(pk + 999, "live") is False
    assert [r.id for r in await manager.list_sessions(kind="rehearsal")] == [pk]
    assert await manager.list_sessions(kind="live") == []


async def test_close_empty_session_discards_row(store: SQLiteDatabase, bus: EventBus) -> None:
    """空场次不留行：开启后无任何明细就结束 → 整行丢弃，事件带 empty_discarded 标记。"""
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
    closed = await manager.close_session()
    assert closed is True
    await bus.wait_until_drained()
    assert await store.sessions.get_live_session(live_session_id=pk) is None
    assert collector.ended and collector.ended[0].empty_discarded is True
    assert collector.ended[0].superseded is False  # 手动下播是真下播
    assert collector.ended[0].deleted is False  # 手动下播不是删除场次
    assert collector.ended[0].duration_ms is not None


async def test_close_session_with_details_keeps_row(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
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
    assert row.ended_at_ms is not None


async def test_close_without_active_returns_false(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()
    assert await manager.close_session() is False


async def test_delete_active_session_closes_first(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
    await manager.delete_session(pk)
    await bus.wait_until_drained()
    assert manager.active_pk is None
    assert collector.ended and collector.ended[0].live_session_id == pk
    assert collector.ended[0].deleted is True


async def test_delete_active_session_with_details_marks_live_ended_as_deleted(
    store: SQLiteDatabase, bus: EventBus
) -> None:
    """删除有对话的进行中场次：先结束（不算空场次丢弃）再删行，live.ended 带"行将被删除"。

    订阅方据此不再往这一场写东西：事件是后台派发，订阅方处理时行多半已删。
    """
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=1_000, sender_role="viewer", content="弹幕", message_type="danmaku"
    )
    assert await manager.delete_session(pk) is True
    await bus.wait_until_drained()

    assert [(p.live_session_id, p.empty_discarded, p.deleted) for p in collector.ended] == [(pk, False, True)]
    assert await store.sessions.get_live_session(live_session_id=pk) is None


async def test_delete_session_cascades_detail_rows(store: SQLiteDatabase, bus: EventBus) -> None:
    """删除场次：live_chat 等明细级联清除；行不存在返回 False。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
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
    assert row.ended_at_ms == 9_000
    assert manager.active_pk is None


async def test_resolve_pk_after_close_returns_none(store: SQLiteDatabase, bus: EventBus) -> None:
    """显式场次结束 → ``resolve_pk()`` 回到 None，无兜底行补建。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await manager.open_session(kind="live")
    assert await manager.resolve_pk() == pk
    await manager.close_session()
    assert await manager.resolve_pk() is None
    # 唯一在场 live_sessions 行是已关闭的 pk（empty_discarded=False 因为没有明细也没显式 insert）
    rows = await store.execute("SELECT * FROM live_sessions WHERE ended_at_ms IS NULL")
    assert rows == [], "无显式场次期间不应有 ended_at_ms IS NULL 的进行中行"


# -------------------- 归属解析（场后发言归最近一场已结束的场次） --------------------


async def _open_with_chat(store: SQLiteDatabase, manager: LiveSessionManager) -> int:
    """开一场并写入一条明细（让结账时行留存，不被当空场次丢弃）。"""
    pk = await manager.open_session(kind="live")
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    return pk


async def test_attribution_prefers_active_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """进行中场次优先：归属解析回退到上一场的同时，进行中场次照旧优先。"""
    manager = _make_manager(store, bus)
    await manager.start()

    await _open_with_chat(store, manager)
    await manager.close_session()
    second = await manager.open_session(kind="live")

    assert await manager.resolve_attribution_pk() == second
    assert await manager.resolve_pk() == second


async def test_attribution_falls_back_to_last_ended_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """场后（上一场已结束、本场未开）→ 归属回退到刚结束的那一场。"""
    manager = _make_manager(store, bus)
    await manager.start()

    pk = await _open_with_chat(store, manager)
    await manager.close_session()
    await bus.wait_until_drained()

    assert await manager.resolve_pk() is None  # 活跃语义：没有进行中场次
    assert await manager.resolve_attribution_pk() == pk  # 归属语义：归刚结束的那一场


async def test_attribution_none_before_first_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """一场都没结束过（第一次开播前）：没有可归属的场次，返回 None。"""
    manager = _make_manager(store, bus)
    await manager.start()

    assert await manager.resolve_attribution_pk() is None


async def test_attribution_skips_discarded_empty_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """空场次整行丢弃 → 这一场的道别句不落库，也不回退到更早那场。

    行没了就没有安全的归属目标：回退到更早那场会把这一场的道别句记到别的
    场次里去，宁可保持无归属（保持 0、不落库），等下一场开播再恢复归属。
    """
    manager = _make_manager(store, bus)
    await manager.start()

    await _open_with_chat(store, manager)
    await manager.close_session()
    await manager.open_session(kind="live")  # 无明细的空场次
    await manager.close_session()
    await bus.wait_until_drained()

    assert await manager.resolve_attribution_pk() is None


async def test_attribution_blocked_after_deleting_active_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """删掉进行中场次 → 归属不落回已删的那场，也不落到更早那场。"""
    manager = _make_manager(store, bus)
    await manager.start()

    earlier = await _open_with_chat(store, manager)
    await manager.close_session()
    deleted = await _open_with_chat(store, manager)
    await bus.wait_until_drained()

    assert await manager.delete_session(deleted) is True
    assert await manager.resolve_attribution_pk() is None
    assert earlier != deleted  # 更早那场确实存在，只是不拿它当归属目标


async def test_attribution_unblocked_after_next_session_starts(store: SQLiteDatabase, bus: EventBus) -> None:
    """行没了导致归属被挡 → 下一场开播解除；该场结束后场后发言照旧归它。"""
    manager = _make_manager(store, bus)
    await manager.start()

    await manager.open_session(kind="live")  # 空场次
    await manager.close_session()
    await bus.wait_until_drained()
    assert await manager.resolve_attribution_pk() is None

    pk = await _open_with_chat(store, manager)
    await manager.close_session()
    await bus.wait_until_drained()

    assert await manager.resolve_attribution_pk() == pk


async def test_attribution_after_deleting_last_ended_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """删掉最近一场已结束的场次 → 归属回退到更早那场。"""
    manager = _make_manager(store, bus)
    await manager.start()

    first = await _open_with_chat(store, manager)
    await manager.close_session()
    second = await _open_with_chat(store, manager)
    await manager.close_session()
    await bus.wait_until_drained()

    assert await manager.resolve_attribution_pk() == second
    assert await manager.delete_session(second) is True
    assert await manager.resolve_attribution_pk() == first


async def test_attribution_loads_last_ended_from_db_after_restart(store: SQLiteDatabase, bus: EventBus) -> None:
    """进程重启后从库里装载最近一场已结束的场次（内存态不跨进程）。"""
    manager = _make_manager(store, bus)
    await manager.start()
    pk = await _open_with_chat(store, manager)
    await manager.close_session()
    await bus.wait_until_drained()

    restarted = _make_manager(store, bus)  # 新进程：新的管理器实例
    await restarted.start()

    assert await restarted.resolve_attribution_pk() == pk
