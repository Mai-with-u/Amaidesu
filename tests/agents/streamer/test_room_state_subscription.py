"""room.state.watched_count 接线：事件订阅把累计观看人次写进主播侧 RoomState 快照。

该事件是纯记账数据源（后台记账经 get_snapshot 写 live_sessions），不进决策缓冲。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room_state import RoomStateWatchedPayload
from tests.agents.streamer.helpers import make_streamer_agent

# 登记（只读/驱动穿透，合并时进底账）：本文件断言读取 Agent 内部件 RoomState 的
# 快照（agent._room_state.get_snapshot）以观察事件处理结果；RoomState 尚无经 Agent
# 的公开只读出口，接缝补齐后即可清除。
_PUSH_TS_MS = 1_700_000_000_000


async def test_watched_count_event_updates_room_state_snapshot() -> None:
    """订阅走 Agent 自己的 _subscribe_events()：总线发 watched_count，快照的累计观看与到达时刻随之更新。"""
    bus = EventBus()
    planner = MagicMock()
    planner.plan = AsyncMock(return_value=None)
    agent = make_streamer_agent(event_bus=bus, planner=planner)
    agent._subscribe_events()
    try:
        await bus.emit(
            CoreEvents.ROOM_STATE_WATCHED_COUNT,
            RoomStateWatchedPayload(platform="bilibili", audience_total=1_234, timestamp_ms=_PUSH_TS_MS),
            source="test",
        )
        await bus.wait_until_drained()

        snapshot = agent._room_state.get_snapshot(now_ms=_PUSH_TS_MS)
        assert (snapshot.audience_total, snapshot.audience_total_at_ms) == (1_234, _PUSH_TS_MS)

        # UV 口径只增不减：后续推送更新到新值与新的到达时刻
        await bus.emit(
            CoreEvents.ROOM_STATE_WATCHED_COUNT,
            RoomStateWatchedPayload(platform="bilibili", audience_total=2_000, timestamp_ms=_PUSH_TS_MS + 60_000),
            source="test",
        )
        await bus.wait_until_drained()
        snapshot = agent._room_state.get_snapshot(now_ms=_PUSH_TS_MS + 60_000)
        assert (snapshot.audience_total, snapshot.audience_total_at_ms) == (2_000, _PUSH_TS_MS + 60_000)
    finally:
        await bus.cleanup()


async def test_watched_count_event_does_not_enter_decision_buffer() -> None:
    """房间状态推送是记账数据源：不进决策消息缓冲、不触发决策回合。"""
    bus = EventBus()
    plan = AsyncMock(return_value=None)
    planner = MagicMock()
    planner.plan = plan
    agent = make_streamer_agent(event_bus=bus, planner=planner)
    agent._subscribe_events()
    try:
        await bus.emit(
            CoreEvents.ROOM_STATE_WATCHED_COUNT,
            RoomStateWatchedPayload(platform="bilibili", audience_total=100, timestamp_ms=_PUSH_TS_MS),
            source="test",
        )
        await bus.wait_until_drained()

        assert agent._buffer.size == 0
        plan.assert_not_awaited()
    finally:
        await bus.cleanup()
