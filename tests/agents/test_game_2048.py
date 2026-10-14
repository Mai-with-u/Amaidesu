"""Game2048Agent（按键驱动 + 事件分流）：懒开局与快照事件、里程碑、终局上报、重开、工具面与名单映射"""

from __future__ import annotations

import asyncio
import random
from typing import List

import pytest

from src.agents.game_2048 import Board2048, Game2048Agent, Game2048Config, MoveDirection
from src.agents.game_2048.tools import Game2048ToolProvider, build_game_2048_visible_to
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GameBoardStatePayload, GamePayload
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry

_DEAD_BOARD = [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]]


class EventCollector:
    """订阅若干事件名收集 payload。EventBus.emit 把回调调度为后续 tick 的 Task，断言前先 ``await settle()``。"""

    def __init__(self, bus: EventBus, spec: dict[str, type]) -> None:
        self.items: List[tuple[str, object]] = []
        for event_name, model_class in spec.items():
            bus.on(event_name, self._collect, model_class=model_class)

    async def _collect(self, event_name: str, payload: object, source: str) -> None:
        self.items.append((event_name, payload))

    async def settle(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0.02)

    def of(self, event_name: str) -> List[object]:
        return [payload for name, payload in self.items if name == event_name]


def _agent(bus: EventBus, seed: int = 7) -> Game2048Agent:
    return Game2048Agent(Game2048Config(), tool_registry=None, event_bus=bus, rng=random.Random(seed))


def _tile_count(agent: Game2048Agent) -> int:
    return sum(1 for row in agent.get_state_snapshot()["board"] for value in row if value)


def test_metadata_declares_the_four_game_event_families() -> None:
    assert Game2048Agent.name == "game_2048" and Game2048Agent.description
    assert {
        CoreEvents.GAME_STATE_CHANGED,
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_REPORT,
        CoreEvents.GAME_ERROR,
    } <= set(Game2048Agent.emits_events)


async def test_first_press_deals_the_board_and_each_move_emits_a_snapshot_with_growing_history(event_bus) -> None:
    states = EventCollector(event_bus, {CoreEvents.GAME_STATE_CHANGED: GameBoardStatePayload})
    agent = _agent(event_bus, seed=11)

    assert (await agent.press(MoveDirection.UP)).moved
    assert _tile_count(agent) == 3  # 懒开局：初始 2 块 + 落子后新块
    await states.settle()
    (first,) = states.of(CoreEvents.GAME_STATE_CHANGED)
    assert (first.game, first.moves, first.last_direction, first.history) == ("game_2048", 1, "up", ["up"])

    await agent.press(MoveDirection.LEFT)
    await states.settle()
    last = states.of(CoreEvents.GAME_STATE_CHANGED)[-1]
    assert (last.last_direction, last.history) == ("left", ["up", "left"])  # 历史旧→新累积


async def test_milestone_is_announced_once_per_tier(event_bus) -> None:
    milestones = EventCollector(event_bus, {CoreEvents.GAME_MILESTONE: GamePayload})
    agent = _agent(event_bus, seed=5)
    agent._board.grid = [[128, 128, 0, 0], [0] * 4, [0] * 4, [0] * 4]  # 残局：一步合成 256

    await agent.press(MoveDirection.LEFT)
    assert agent.get_state_snapshot()["max_tile"] == 256
    await milestones.settle()
    assert len(milestones.of(CoreEvents.GAME_MILESTONE)) == 1

    await agent.press(MoveDirection.UP)  # 同档再落子不重复播
    await milestones.settle()
    assert len(milestones.of(CoreEvents.GAME_MILESTONE)) == 1


async def test_pressing_on_a_dead_board_ends_the_game_and_reports_delivery_exactly_once(event_bus) -> None:
    reports = EventCollector(event_bus, {CoreEvents.GAME_REPORT: GamePayload})
    agent = _agent(event_bus, seed=5)
    agent._board.grid = [list(row) for row in _DEAD_BOARD]

    assert not (await agent.press(MoveDirection.LEFT)).moved
    assert agent.get_state_snapshot()["over"] is True
    await reports.settle()
    (report,) = reports.of(CoreEvents.GAME_REPORT)
    assert report.report_kind == "delivery"

    await agent.press(MoveDirection.LEFT)  # 对终局再按键不重复上报
    await reports.settle()
    assert len(reports.of(CoreEvents.GAME_REPORT)) == 1


async def test_restart_clears_the_board_and_emits_a_fresh_frame(event_bus) -> None:
    states = EventCollector(event_bus, {CoreEvents.GAME_STATE_CHANGED: GameBoardStatePayload})
    agent = _agent(event_bus, seed=9)

    await agent.press(MoveDirection.LEFT)
    await agent.restart()

    snapshot = agent.get_state_snapshot()
    assert (snapshot["moves"], snapshot["over"], _tile_count(agent)) == (0, False, 2)
    await states.settle()
    frames = states.of(CoreEvents.GAME_STATE_CHANGED)
    assert len(frames) == 2 and frames[-1].last_direction is None and frames[-1].history == []  # 重开帧：方向与历史清空


@pytest.fixture
def provider(event_bus) -> Game2048ToolProvider:
    return Game2048ToolProvider(agent=_agent(event_bus, seed=21))


def _press(provider: Game2048ToolProvider, key: str):
    return provider.invoke(ToolInvocation(tool_name="game_2048_press", arguments={"key": key}))


async def test_press_tool_rejects_unknown_keys_and_renders_moves_and_restarts(provider) -> None:
    invalid = await _press(provider, "diagonal")
    assert not invalid.success and "key" in invalid.error_message

    moved = await _press(provider, "left")
    assert moved.success and "score=" in moved.content and moved.structured_content["moves"] == 1

    restarted = await _press(provider, "restart")
    assert restarted.success and restarted.structured_content["moves"] == 0


async def test_get_state_tool_returns_the_snapshot_shape(provider) -> None:
    snapshot = await provider.invoke(ToolInvocation(tool_name="game_2048_get_state", arguments={}))

    assert snapshot.success and {"board", "score", "moves", "max_tile", "over"} <= set(snapshot.structured_content)


def test_both_tools_are_visible_to_the_streamer_only() -> None:
    visible_to = build_game_2048_visible_to()

    assert set(visible_to) == {"game_2048_press", "game_2048_get_state"}
    assert all(agents == ["streamer"] for agents in visible_to.values())


def test_board_new_game_spawns_two_tiles() -> None:
    board = Board2048(rng=random.Random(42))
    board.new_game()

    assert sum(1 for row in board.grid for value in row if value) == 2 and board.moves == 0


# ---------------------------------------------------------------------------
# 工具面使用说明：告诉主播这是遥控型游戏
# ---------------------------------------------------------------------------


def test_tool_guide_declares_remote_control_shape() -> None:
    """玩法说明点名两个工具、说明没有后台任务、不走委派；经注册表按名单可取。"""
    agent = Game2048Agent(Game2048Config(), rng=random.Random(1))
    provider = Game2048ToolProvider(agent=agent)
    guide = provider.guide_for("streamer")
    assert "遥控型" in guide and "game_2048_press" in guide and "game_2048_get_state" in guide
    assert "framework_delegate" in guide and "没有后台任务" in guide

    registry = ToolRegistry()
    registry.register_provider(provider, visible_to=build_game_2048_visible_to())
    assert registry.guides(for_agent="streamer") == [("game_2048", guide)]
    assert registry.guides(for_agent="minecraft") == []
