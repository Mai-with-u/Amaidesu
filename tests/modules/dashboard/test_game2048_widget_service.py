"""Game2048WidgetService 测试（game.state.changed 快照订阅与单槽 history）

覆盖：
- 事件订阅：start 后收到 game.state.changed → 单槽快照更新 + 回调广播
- 单槽语义：新快照覆盖旧快照（不累积队列）
- history：get_latest_state 供 WS 接入时恢复当前棋盘；无快照为 None
- 生命周期：stop 摘除订阅（后续事件不再消费）
"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GameBoardStatePayload


def _make_state(moves: int, score: int = 128, over: bool = False) -> GameBoardStatePayload:
    return GameBoardStatePayload(
        game="game_2048",
        board=[[2, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2]],
        score=score,
        moves=moves,
        max_tile=4,
        over=over,
    )


async def _emit(bus: EventBus, payload: GameBoardStatePayload) -> None:
    await bus.emit(CoreEvents.GAME_STATE_CHANGED, payload, source="test")
    # EventBus 回调为后续 tick 的 Task：让出循环等落地
    for _ in range(3):
        import asyncio

        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_subscription_updates_latest_and_broadcasts() -> None:
    bus = EventBus()
    svc = Game2048WidgetService(event_bus=bus)
    received: list[dict[str, Any]] = []
    svc.set_state_callback(received.append)

    await svc.start()
    assert svc.is_running
    await _emit(bus, _make_state(moves=1))

    latest = svc.get_latest_state()
    assert latest is not None
    assert latest["moves"] == 1
    assert latest["game"] == "game_2048"
    assert len(received) == 1
    assert received[0]["type"] == "state"
    assert received[0]["state"]["moves"] == 1

    await svc.stop()


@pytest.mark.asyncio
async def test_single_slot_overwrites_previous_state() -> None:
    bus = EventBus()
    svc = Game2048WidgetService(event_bus=bus)
    received: list[dict[str, Any]] = []
    svc.set_state_callback(received.append)

    await svc.start()
    await _emit(bus, _make_state(moves=1))
    await _emit(bus, _make_state(moves=2, over=True))

    assert svc.get_latest_state() is not None
    assert svc.get_latest_state()["moves"] == 2  # type: ignore[index]
    assert len(received) == 2  # 单槽快照、逐帧广播，不累积历史队列

    await svc.stop()


@pytest.mark.asyncio
async def test_latest_is_none_before_any_state() -> None:
    svc = Game2048WidgetService(event_bus=EventBus())
    assert svc.get_latest_state() is None
    assert svc.get_stats() == {"is_running": False, "has_state": False, "score": 0}


@pytest.mark.asyncio
async def test_stop_unsubscribes_from_events() -> None:
    bus = EventBus()
    svc = Game2048WidgetService(event_bus=bus)
    received: list[dict[str, Any]] = []
    svc.set_state_callback(received.append)

    await svc.start()
    await svc.stop()
    assert not svc.is_running

    await _emit(bus, _make_state(moves=1))
    assert svc.get_latest_state() is None
    assert received == []
