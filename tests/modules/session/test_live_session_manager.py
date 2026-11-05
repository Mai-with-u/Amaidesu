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
import time
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


async def test_startup_marks_dangling_sessions_pending(store: SQLiteDatabase, bus: EventBus) -> None:
    """上次进程未正常退出的"进行中"显式场次，启动时标为待决（不自动收口）。

    收口还是续播交用户拍板：行保持未结账态、不广播事件；待决期间
    ``resolve_pk()`` 返回 None，下游落库路径照旧跳过。
    """
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )

    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()
    await bus.wait_until_drained()

    assert manager.pending_residual_pks == [dangling]
    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None
    assert row.ended_at_ms is None, "待决残留不得被启动自动收口"
    assert manager.active_pk is None
    assert collector.started == [], "待决残留不广播 live.started"


async def test_resume_pending_session_reactivates_it(store: SQLiteDatabase, bus: EventBus) -> None:
    """续播待决残留：接回为进行中场次并广播 live.started(resumed=True)。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )
    await store.chat.insert_live_chat(
        live_session_id=dangling, timestamp_ms=2_000, sender_role="viewer", content="断前的弹幕", message_type="danmaku"
    )

    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    assert await manager.resume_session(dangling) is True
    await bus.wait_until_drained()

    assert manager.active_pk == dangling
    assert manager.active_kind == "live"
    assert manager.pending_residual_pks == []
    assert await manager.resolve_pk() == dangling
    assert await manager.resolve_attribution_pk() == dangling
    assert collector.started and collector.started[0].live_session_id == dangling
    assert collector.started[0].resumed is True, "续播的 live.started 必须带 resumed 标记"
    assert collector.started[0].started_at_ms == 1_000, "开播时刻取原行的开播时刻，不是续播时刻"
    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None and row.ended_at_ms is None, "续播不写结束时间，行保持进行中"


async def test_resume_then_close_settles_normally(store: SQLiteDatabase, bus: EventBus) -> None:
    """续播后的场次走正常结账：close_session 补结束时间并广播 live.ended（真下播）。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.chat.insert_live_chat(
        live_session_id=dangling, timestamp_ms=2_000, sender_role="viewer", content="断前的弹幕", message_type="danmaku"
    )
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    assert await manager.resume_session(dangling) is True
    assert await manager.close_session() is True
    await bus.wait_until_drained()

    assert manager.active_pk is None
    assert collector.ended and collector.ended[0].live_session_id == dangling
    assert collector.ended[0].superseded is False, "续播后手动结账是真下播"


async def test_resume_rejects_when_active_session_exists(store: SQLiteDatabase, bus: EventBus) -> None:
    """已有进行中场次时拒绝续播（不变量：至多一个显式进行中场次）。

    可达场景：两场残留同时待决，先续播一场、再续播另一场被拒——被拒的那场
    保持待决，已续播的场不受影响。
    """
    first = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    second = await store.sessions.insert_live_session(started_at_ms=2_000, source="manual")
    manager = _make_manager(store, bus)
    await manager.start()
    assert manager.pending_residual_pks == [first, second]

    assert await manager.resume_session(first) is True
    assert await manager.resume_session(second) is False

    assert manager.active_pk == first, "拒绝续播不得影响已在进行中的场次"
    assert manager.pending_residual_pks == [second], "被拒的残留场次保持待决"


async def test_resume_unknown_session_returns_false(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()
    assert await manager.resume_session(4242) is False


async def test_discard_pending_residual_backfills_end_time(store: SQLiteDatabase, bus: EventBus) -> None:
    """收尾待决残留：补结束时间为最后活动时刻，不广播任何事件（本进程从未在播这场）。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    assert await manager.discard_residual(dangling) is True
    await bus.wait_until_drained()

    assert manager.pending_residual_pks == []
    assert manager.active_pk is None
    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None and row.ended_at_ms == 9_000
    assert collector.ended == [], "收尾残留不广播 live.ended（主播从未在播这一场）"


async def test_discard_unknown_residual_returns_false(store: SQLiteDatabase, bus: EventBus) -> None:
    manager = _make_manager(store, bus)
    await manager.start()
    assert await manager.discard_residual(4242) is False


async def test_discard_clamps_end_time_when_last_activity_precedes_start(store: SQLiteDatabase, bus: EventBus) -> None:
    """收尾钳制：残留行的最后活动时刻早于开播时刻（时钟回拨/脏数据）时以开播时刻封账。

    直写会撞库表 CHECK 约束（ended_at_ms >= started_at_ms）使整笔收尾 500。
    """
    future_start = (
        int(time.time() * 1000) + 3_600_000
    )  # 真实时刻 +1 小时：晚于 updated_at_ms 的"未来"开播时刻（绝对写死会随钟表失效）
    dangling = await store.sessions.insert_live_session(started_at_ms=future_start, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=0, viewer_count=0, audience_total=0, updated_at_ms=future_start - 60_000
    )
    manager = _make_manager(store, bus)
    await manager.start()

    assert await manager.discard_residual(dangling) is True

    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None
    assert row.ended_at_ms == future_start, "结束时刻钳到开播时刻，不早于它"


async def test_close_after_resume_clamps_end_time_before_future_start(store: SQLiteDatabase, bus: EventBus) -> None:
    """续播后结账的钳制：开播时刻在未来、当下时刻早于它时以开播时刻封账，时长记 0。"""
    future_start = int(time.time() * 1000) + 3_600_000  # 真实时刻 +1 小时，保证测试运行时"当下早于开播"恒成立
    dangling = await store.sessions.insert_live_session(started_at_ms=future_start, source="manual")
    await store.chat.insert_live_chat(
        live_session_id=dangling, timestamp_ms=future_start, sender_role="viewer", content="hi", message_type="danmaku"
    )
    manager = _make_manager(store, bus)
    collector = _Collector(bus)
    await manager.start()

    assert await manager.resume_session(dangling) is True
    assert await manager.close_session() is True
    await bus.wait_until_drained()

    row = await store.sessions.get_live_session(live_session_id=dangling)
    assert row is not None and row.ended_at_ms == future_start
    assert collector.ended and collector.ended[0].duration_ms == 0, "钳制后时长记 0，不为负"


async def test_open_session_auto_discards_pending_residuals(store: SQLiteDatabase, bus: EventBus) -> None:
    """开新场 = 放弃续播：待决残留自动收尾，不留悬行。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    await store.sessions.update_live_session_stats(
        live_session_id=dangling, heat=1, viewer_count=0, audience_total=0, updated_at_ms=9_000
    )
    manager = _make_manager(store, bus)
    await manager.start()
    assert manager.pending_residual_pks == [dangling]

    new_pk = await manager.open_session(kind="live")

    assert manager.pending_residual_pks == []
    old_row = await store.sessions.get_live_session(live_session_id=dangling)
    assert old_row is not None and old_row.ended_at_ms == 9_000
    assert manager.active_pk == new_pk


async def test_delete_pending_residual_removes_it_from_pending(store: SQLiteDatabase, bus: EventBus) -> None:
    """删掉待决残留场次：待决标记同步移除，不再出现在续播弹窗里。"""
    dangling = await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    manager = _make_manager(store, bus)
    await manager.start()
    assert manager.pending_residual_pks == [dangling]

    assert await manager.delete_session(dangling) is True

    assert manager.pending_residual_pks == []


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


async def test_set_session_kind_moves_ledger_in_the_same_call(store: SQLiteDatabase, bus: EventBus) -> None:
    """正式场改成彩排：类型、档案行、本场承诺与整理进度在同一次调用里一起处理。"""
    manager = _make_manager(store, bus)
    await manager.start()
    pk = await manager.open_session(kind="live")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=1_100, sender_role="viewer", content="hi", message_type="danmaku"
    )
    await manager.close_session()
    await store.archive.insert_line(live_session_id=pk, text="正式场档案", quote="正式场档案", evidence_chat_id=1)
    await store.commitments.insert_commitment(what="正式场承诺", created_session_id=pk)
    await store.sessions.mark_settle_done(live_session_id=pk, settled_at_ms=6_000)

    assert await manager.set_session_kind(pk, "rehearsal") is True

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.kind == "rehearsal" and row.settled_at_ms is None
    assert await store.archive.list_lines(live_session_id=pk) == []
    assert await store.commitments.count_commitments() == 0


async def test_set_session_kind_requeues_migrated_rehearsal(store: SQLiteDatabase, bus: EventBus) -> None:
    """v24 把存量场次标成已整理（完成时刻 == 结账时刻）；改回正式场要重新排队。"""
    manager = _make_manager(store, bus)
    await manager.start()
    pk = await manager.open_session(kind="rehearsal")
    await store.chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=1_100, sender_role="viewer", content="hi", message_type="danmaku"
    )
    assert await manager.close_session() is True
    ended = await store.sessions.get_live_session(live_session_id=pk)
    assert ended is not None and ended.ended_at_ms is not None
    await store.sessions.mark_settle_done(live_session_id=pk, settled_at_ms=int(ended.ended_at_ms))

    assert await manager.set_session_kind(pk, "live") is True

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert row is not None and row.kind == "live" and row.settled_at_ms is None
    pending = await store.sessions.list_sessions_pending_settle(limit=10)
    assert [item.id for item in pending] == [pk]
