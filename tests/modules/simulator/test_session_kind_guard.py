"""SimulatorService 与场次：采集器形态注入，正式场是唯一禁区（ADR-050 决策 3 机制修订）。

用真实的 LiveSessionManager（临时库）驱动：
- 无场次时照常启动（调试注入，消息不落库）
- 彩排场进行中附着注入，停止时不替人收口
- 正式场进行中拒绝启动；运行中遇正式场开播自动停下
- 彩排场中途结束（无后继）不停——回到无场次的调试注入
"""

from __future__ import annotations

from typing import AsyncGenerator, Tuple

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.session import LiveSessionManager
from src.modules.simulator import SimulatorService
from src.modules.storage.database import SQLiteDatabase
from tests.modules.simulator.helpers import FakeLLMService, enabled_config

# 固定节奏、零暖场、只走弹幕：启动后不需要等待真实时间
_QUIET = {"cadence_mode": "fixed", "fixed_interval_s": 60.0, "gift_probability": 0.0, "warmup_duration_s": 0.0}


@pytest.fixture
async def rig(store: SQLiteDatabase) -> AsyncGenerator[Tuple[SimulatorService, LiveSessionManager, EventBus], None]:
    bus = EventBus()
    manager = LiveSessionManager(store.sessions, store.chat, bus, platform="bilibili")
    await manager.start()
    llm = FakeLLMService()
    service = SimulatorService(
        event_bus=bus,
        sim_repo=store.sim,
        chat_repo=store.chat,
        services_by_type={type(llm): llm},
        session_manager=manager,
    )
    await service.setup(enabled_config(mode="generate", **_QUIET), auto_start=False)
    yield service, manager, bus
    await service.cleanup()
    await bus.cleanup()


async def test_start_succeeds_without_any_session(
    rig: Tuple[SimulatorService, LiveSessionManager, EventBus],
) -> None:
    """采集器形态：无场次时照常启动（调试注入，消息不落库），不代开场次。"""
    service, manager, _ = rig

    assert await service.start() is None
    assert service.is_running is True
    assert manager.active_pk is None  # 模拟器不代开场次


async def test_generate_attaches_to_manual_rehearsal_without_closing_it(
    rig: Tuple[SimulatorService, LiveSessionManager, EventBus],
) -> None:
    service, manager, _ = rig
    pk = await manager.open_session(kind="rehearsal", title="人手开的彩排")

    assert await service.start() is None
    assert manager.active_pk == pk
    await service.stop()
    assert manager.active_pk == pk  # 不是自己开的，不替人收口


async def test_start_rejected_while_live_session_is_active(
    rig: Tuple[SimulatorService, LiveSessionManager, EventBus],
) -> None:
    service, manager, _ = rig
    pk = await manager.open_session(kind="live", title="正式直播")

    reason = await service.start()
    assert reason is not None and "正式场次" in reason
    assert service.is_running is False
    assert (manager.active_pk, manager.active_kind) == (pk, "live")


async def test_opening_live_session_stops_simulator_and_keeps_live_open(
    rig: Tuple[SimulatorService, LiveSessionManager, EventBus],
) -> None:
    service, manager, bus = rig
    rehearsal_pk = await manager.open_session(kind="rehearsal", title="人手开的彩排")
    assert await service.start() is None

    live_pk = await manager.open_session(kind="live", title="正式开播")
    await bus.wait_until_drained()

    assert service.is_running is False
    assert live_pk != rehearsal_pk
    assert (manager.active_pk, manager.active_kind) == (live_pk, "live")  # 正式场没被模拟器收掉


async def test_rehearsal_closed_midrun_keeps_injecting_without_session(
    rig: Tuple[SimulatorService, LiveSessionManager, EventBus],
) -> None:
    """彩排场中途结束且无后继 → 回到无场次的调试注入，模拟器不停。"""
    service, manager, _ = rig
    await manager.open_session(kind="rehearsal", title="人手开的彩排")
    assert await service.start() is None

    await manager.close_session()

    assert manager.active_pk is None
    assert service.is_running is True  # 采集器形态：无场次不停，只是不再落库
