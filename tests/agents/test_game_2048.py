"""Game2048Agent 测试（按键驱动 + 事件分流）

覆盖：
- 元数据：name / description / emits_events（game.state.changed 等 4 族）
- press 懒开局：首次按键自动发牌 + 发 game.state.changed 快照
- 里程碑：首次合成出 ≥ 阈值档位发 game.milestone，同档不重复播
- 终局：死局按键 → game.report（delivery）恰好一条 + over 置位
- restart：重开清盘 + 快照事件 + 终局标志复位
- 工具面：press 非法 key 失败结果、press 方向键、press restart、
  get_state 快照形状、名单映射（press/get_state 两工具，全 ["streamer"]）
"""

from __future__ import annotations

import asyncio
import random
from typing import List, Optional

import pytest

from src.agents.game_2048 import (
    Board2048,
    Game2048Agent,
    Game2048Config,
    MoveDirection,
)
from src.agents.game_2048.tools import (
    Game2048ToolProvider,
    build_game_2048_visible_to,
)
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GameBoardStatePayload, GamePayload
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 事件收集辅助
# ---------------------------------------------------------------------------


class EventCollector:
    """订阅若干事件名收集 (event_name, payload)，供断言。

    EventBus.emit 把回调调度为后续 tick 的 Task——断言事件数前先
    ``await settle()`` 让出事件循环等回调落地。
    """

    def __init__(self, bus: EventBus, spec: dict[str, type]) -> None:
        self.items: List[tuple[str, object]] = []
        for event_name, model_class in spec.items():
            bus.on(event_name, self._collect, model_class=model_class)

    async def _collect(self, event_name: str, payload: object, source: str) -> None:
        self.items.append((event_name, payload))

    async def settle(self) -> None:
        """让出事件循环若干轮，等待已 emit 事件的回调 Task 执行完。"""
        for _ in range(3):
            await asyncio.sleep(0.02)

    def of(self, event_name: str) -> List[object]:
        return [payload for name, payload in self.items if name == event_name]

    def first_of(self, event_name: str) -> Optional[object]:
        for name, payload in self.items:
            if name == event_name:
                return payload
        return None


def _make_agent(
    bus: EventBus,
    *,
    registry: bool = False,
    rng_seed: int = 7,
) -> Game2048Agent:
    tool_registry = ToolRegistry() if registry else None
    return Game2048Agent(
        Game2048Config(),
        tool_registry=tool_registry,
        event_bus=bus,
        rng=random.Random(rng_seed),
    )


# ---------------------------------------------------------------------------
# 元数据与事件族
# ---------------------------------------------------------------------------


def test_metadata_and_events() -> None:
    assert Game2048Agent.name == "game_2048"
    assert Game2048Agent.description
    assert CoreEvents.GAME_STATE_CHANGED in Game2048Agent.emits_events
    assert CoreEvents.GAME_MILESTONE in Game2048Agent.emits_events
    assert CoreEvents.GAME_REPORT in Game2048Agent.emits_events
    assert CoreEvents.GAME_ERROR in Game2048Agent.emits_events


# ---------------------------------------------------------------------------
# press：懒开局 / 快照事件 / 里程碑
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_starts_game_and_emits_state() -> None:
    bus = EventBus()
    collector = EventCollector(bus, {CoreEvents.GAME_STATE_CHANGED: GameBoardStatePayload})
    agent = _make_agent(bus, rng_seed=11)

    outcome = await agent.press(MoveDirection.UP)
    assert outcome.moved
    tiles = [v for row in agent.get_state_snapshot()["board"] for v in row if v != 0]
    assert len(tiles) == 3  # 初始 2 块 + 落子后新块

    await collector.settle()
    states = collector.of(CoreEvents.GAME_STATE_CHANGED)
    assert len(states) == 1
    payload = states[0]
    assert isinstance(payload, GameBoardStatePayload)
    assert payload.game == "game_2048"
    assert payload.moves == 1
    assert payload.last_direction == "up"
    assert payload.history == ["up"]

    # 第二步：历史累积（旧→新）
    await agent.press(MoveDirection.LEFT)
    await collector.settle()
    payload2 = collector.of(CoreEvents.GAME_STATE_CHANGED)[-1]
    assert isinstance(payload2, GameBoardStatePayload)
    assert payload2.last_direction == "left"
    assert payload2.history == ["up", "left"]


@pytest.mark.asyncio
async def test_milestone_emits_once_per_tier() -> None:
    bus = EventBus()
    collector = EventCollector(bus, {CoreEvents.GAME_MILESTONE: GamePayload})
    agent = _make_agent(bus, rng_seed=5)
    agent._board.grid = [  # noqa: SLF001 - 构造可合并 256 的残局
        [128, 128, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]

    await agent.press(MoveDirection.LEFT)
    assert agent.get_state_snapshot()["max_tile"] == 256
    await collector.settle()
    assert len(collector.of(CoreEvents.GAME_MILESTONE)) == 1

    # 同档再落子不重复播
    await agent.press(MoveDirection.UP)
    await collector.settle()
    assert len(collector.of(CoreEvents.GAME_MILESTONE)) == 1


@pytest.mark.asyncio
async def test_press_after_over_is_noop() -> None:
    bus = EventBus()
    agent = _make_agent(bus, rng_seed=5)
    agent._board.grid = [  # noqa: SLF001 - 直接构造死局
        [2, 4, 2, 4],
        [4, 2, 4, 2],
        [2, 4, 2, 4],
        [4, 2, 4, 2],
    ]
    outcome = await agent.press(MoveDirection.LEFT)
    assert not outcome.moved
    assert agent.get_state_snapshot()["over"] is True


@pytest.mark.asyncio
async def test_invalid_move_dead_board_emits_report_once() -> None:
    bus = EventBus()
    collector = EventCollector(bus, {CoreEvents.GAME_REPORT: GamePayload})
    agent = _make_agent(bus, rng_seed=5)
    agent._board.grid = [  # noqa: SLF001 - 直接构造死局
        [2, 4, 2, 4],
        [4, 2, 4, 2],
        [2, 4, 2, 4],
        [4, 2, 4, 2],
    ]

    outcome = await agent.press(MoveDirection.LEFT)
    assert not outcome.moved
    # 死局按键：终局判定 + delivery 上报恰好一次
    assert agent.get_state_snapshot()["over"] is True
    await collector.settle()
    reports = collector.of(CoreEvents.GAME_REPORT)
    assert len(reports) == 1
    payload = reports[0]
    assert isinstance(payload, GamePayload)
    assert payload.report_kind == "delivery"

    # 对终局再按键不重复上报
    await agent.press(MoveDirection.LEFT)
    await collector.settle()
    assert len(collector.of(CoreEvents.GAME_REPORT)) == 1


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_resets_board() -> None:
    bus = EventBus()
    collector = EventCollector(bus, {CoreEvents.GAME_STATE_CHANGED: GameBoardStatePayload})
    agent = _make_agent(bus, rng_seed=9)

    await agent.press(MoveDirection.LEFT)
    await agent.restart()
    snapshot = agent.get_state_snapshot()
    assert snapshot["moves"] == 0
    assert snapshot["over"] is False
    tiles = [v for row in snapshot["board"] for v in row if v != 0]
    assert len(tiles) == 2
    await collector.settle()
    states = collector.of(CoreEvents.GAME_STATE_CHANGED)
    assert len(states) == 2
    # 重开帧：方向为 None、历史清空
    last = states[-1]
    assert isinstance(last, GameBoardStatePayload)
    assert last.last_direction is None
    assert last.history == []


# ---------------------------------------------------------------------------
# 工具面
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_press_invalid_key_fails() -> None:
    bus = EventBus()
    agent = _make_agent(bus, rng_seed=7)
    provider = Game2048ToolProvider(agent=agent)

    result = await provider.invoke(ToolInvocation(tool_name="game_2048_press", arguments={"key": "diagonal"}))
    assert not result.success
    assert result.error_message is not None and "key" in result.error_message


@pytest.mark.asyncio
async def test_tool_press_move_returns_board_render() -> None:
    bus = EventBus()
    agent = _make_agent(bus, rng_seed=21)
    provider = Game2048ToolProvider(agent=agent)

    result = await provider.invoke(ToolInvocation(tool_name="game_2048_press", arguments={"key": "left"}))
    assert result.success
    assert result.content is not None and "score=" in result.content
    structured = result.structured_content or {}
    assert structured["moves"] == 1


@pytest.mark.asyncio
async def test_tool_press_restart_and_get_state() -> None:
    bus = EventBus()
    agent = _make_agent(bus, rng_seed=21)
    provider = Game2048ToolProvider(agent=agent)

    restart = await provider.invoke(ToolInvocation(tool_name="game_2048_press", arguments={"key": "restart"}))
    assert restart.success
    structured = restart.structured_content or {}
    assert structured["moves"] == 0

    snapshot = await provider.invoke(ToolInvocation(tool_name="game_2048_get_state", arguments={}))
    assert snapshot.success
    keys = set((snapshot.structured_content or {}).keys())
    assert {"board", "score", "moves", "max_tile", "over"} <= keys


def test_visible_to_all_streamer_only() -> None:
    visible_to = build_game_2048_visible_to()
    assert all(agents == ["streamer"] for agents in visible_to.values())
    assert set(visible_to.keys()) == {"game_2048_press", "game_2048_get_state"}


# ---------------------------------------------------------------------------
# 规则引擎（board.py 归属本包的补充断言）
# ---------------------------------------------------------------------------


def test_board_new_game_spawns_two_tiles() -> None:
    board = Board2048(rng=random.Random(42))
    board.new_game()
    tiles = [v for row in board.grid for v in row if v != 0]
    assert len(tiles) == 2
    assert board.moves == 0
