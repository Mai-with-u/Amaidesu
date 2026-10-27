"""进程退出兜底收工：活跃场次在退出路径上补账，正常下播流程之外的最后防线。

覆盖两层：
- ``LiveSessionManager.close_session``（既有幂等收尾入口）在兜底 reason
  下的落账 / 幂等衔接 / 无场次静默；
- ``main.fallback_close_active_session``（main() finally 位的兜底函数）
  的 None 静默与异常不阻断。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

import main as app_main
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload
from src.modules.session import LiveSessionManager
from src.modules.storage.database import SQLiteDatabase

FALLBACK_REASON = "进程退出兜底收工"


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="session-exit-fallback-"))
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


class _EndedCollector:
    """live.ended 事件收集器。"""

    def __init__(self, bus: EventBus) -> None:
        self.ended: list[LiveEndedPayload] = []
        bus.on(CoreEvents.LIVE_ENDED, self._on_ended, model_class=LiveEndedPayload)

    async def _on_ended(self, name: str, payload: LiveEndedPayload, source: str) -> None:
        self.ended.append(payload)


async def _open_session_with_detail(store: SQLiteDatabase, manager: LiveSessionManager) -> int:
    """开启一个带明细行的活跃场次（避免空场次丢弃语义干扰落账断言）。"""
    pk = await manager.open_session(title="兜底收工测试场")
    await store.chat.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="弹幕",
        message_type="danmaku",
    )
    return pk


async def test_exit_fallback_settles_active_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """兜底触发：活跃场次经 close_session 落账，ended_at_ms 写库 + live.ended 带 reason。"""
    manager = _make_manager(store, bus)
    collector = _EndedCollector(bus)
    await manager.start()

    pk = await _open_session_with_detail(store, manager)
    closed = await manager.close_session(reason=FALLBACK_REASON)
    assert closed is True
    await asyncio.sleep(0.02)

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None
    assert row.ended_at_ms is not None, "兜底收工必须补写 ended_at_ms"
    assert collector.ended and collector.ended[0].reason == FALLBACK_REASON
    assert collector.ended[0].live_session_id == pk


async def test_repeated_exit_does_not_settle_twice(store: SQLiteDatabase, bus: EventBus) -> None:
    """重复退出（run_shutdown 常规结账后再触发兜底）不二次落账、不二次广播。"""
    manager = _make_manager(store, bus)
    collector = _EndedCollector(bus)
    await manager.start()

    pk = await _open_session_with_detail(store, manager)
    assert await manager.close_session(reason="进程退出") is True
    first_ended = (await store.sessions.get_live_session(live_session_id=pk)).ended_at_ms
    await asyncio.sleep(0.02)
    assert len(collector.ended) == 1

    # 兜底再调一次：内存态已复位 → False 静默，账目不动
    assert await manager.close_session(reason=FALLBACK_REASON) is False
    await asyncio.sleep(0.02)
    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.ended_at_ms == first_ended
    assert len(collector.ended) == 1, "重复退出不得产生第二条 live.ended"


async def test_exit_fallback_silent_without_active_session(store: SQLiteDatabase, bus: EventBus) -> None:
    """无活跃场次时兜底静默通过：返回 False，不建行、不发事件。"""
    manager = _make_manager(store, bus)
    collector = _EndedCollector(bus)
    await manager.start()

    assert await manager.close_session(reason=FALLBACK_REASON) is False
    rows = await store.execute("SELECT * FROM live_sessions")
    assert rows == []
    assert collector.ended == []


async def test_close_session_idempotent_when_row_already_settled(store: SQLiteDatabase, bus: EventBus) -> None:
    """幂等守卫：行已被外部结账（如上次收尾中断残留内存态）→ 复位静默返回，
    不重复广播 live.ended、不误删行。"""
    manager = _make_manager(store, bus)
    collector = _EndedCollector(bus)
    await manager.start()

    pk = await _open_session_with_detail(store, manager)
    # 绕过 manager 直接在仓储层结账：模拟上次收尾在事件广播前被打断
    started = (await store.sessions.get_live_session(live_session_id=pk)).started_at_ms
    await store.sessions.close_live_session(live_session_id=pk, ended_at_ms=int(started) + 5_000)
    settled = await store.sessions.get_live_session(live_session_id=pk)
    assert settled is not None and settled.ended_at_ms == int(started) + 5_000

    assert await manager.close_session(reason=FALLBACK_REASON) is False
    await asyncio.sleep(0.02)
    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None, "已结账行不得被兜底路径误删"
    assert row.ended_at_ms == int(started) + 5_000, "已结账行不得被兜底路径改写"
    assert collector.ended == [], "已结账行不得再次广播 live.ended"
    assert manager.active_pk is None, "内存态应被复位"


class _ExplodingManager:
    """close_session 必抛异常的假管理器：验证兜底吞异常不阻断退出。"""

    def __init__(self) -> None:
        self.calls = 0

    async def close_session(self, *, reason: str) -> bool:
        self.calls += 1
        raise RuntimeError(f"db closed（reason={reason}）")


async def test_fallback_helper_skips_none_manager() -> None:
    """session_manager 未装配（None）时兜底静默通过。"""
    await app_main.fallback_close_active_session(None)


async def test_fallback_helper_swallows_exception() -> None:
    """兜底函数自身异常只吞不抛——不得阻断退出路径。"""
    exploding = _ExplodingManager()
    await app_main.fallback_close_active_session(exploding)  # 不应抛出
    assert exploding.calls == 1, "兜底必须真实尝试过 close_session"


async def test_fallback_helper_settles_via_real_manager(store: SQLiteDatabase, bus: EventBus) -> None:
    """兜底函数经真实管理器走完整链路：活跃场次被补账。"""
    manager = _make_manager(store, bus)
    await manager.start()
    pk = await _open_session_with_detail(store, manager)

    await app_main.fallback_close_active_session(manager)

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None
    assert row.ended_at_ms is not None


class _StubRecorder:
    """最小事件历史记录器替身：只在 stop 时快照 live.ended 是否已到达。"""

    def __init__(self, collector: _EndedCollector) -> None:
        self._collector = collector
        self.event_history = None
        self.diagnostics_writer = None
        self.ended_seen_at_stop = False

    async def stop(self) -> None:
        # emit 是并发扇出、不等订阅者完成的：让出循环，pending 的 handler 跑完再快照
        await asyncio.sleep(0.05)
        self.ended_seen_at_stop = bool(self._collector.ended)


async def test_shutdown_settles_session_before_event_telemetry_goes_down(store: SQLiteDatabase, bus: EventBus) -> None:
    """收尾链顺序：场次结账的 live.ended 必须在事件历史记录器停止之前发出。

    结账若排在记录器/总线拆除之后（如兜底挂在 main() finally 位而常规
    结账在总线 cleanup 之后），事件流里就缺下播标记，回看时间线断档。
    """
    collector = _EndedCollector(bus)
    recorder = _StubRecorder(collector)
    manager = _make_manager(store, bus)
    await manager.start()
    pk = await _open_session_with_detail(store, manager)

    await app_main.run_shutdown(
        bus,
        None,
        None,
        recorder,
        None,
        None,
        None,
        session_manager=manager,
    )

    assert collector.ended and collector.ended[0].reason == "进程退出", "退出收尾必须广播 live.ended"
    assert collector.ended[0].live_session_id == pk
    assert recorder.ended_seen_at_stop, "live.ended 必须在事件历史记录器停止之前到达"

    # 兜底幂等守卫仍在：常规结账后再触发兜底不重复广播
    await app_main.fallback_close_active_session(manager)
    await asyncio.sleep(0.05)
    assert len(collector.ended) == 1, "兜底不得产生第二条 live.ended"
