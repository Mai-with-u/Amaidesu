"""agenda.update 事件发布测试。

发布点在 StreamerAgent（Agent 层是事件上报面）：环节推进/手动控制后广播
``agenda.update``，Dashboard 前端订阅该事件触发节目单快照重拉。

覆盖：
- agenda_control skip → 旧环节 done 事件
- agenda_control jump → schedule 事件
- 推进回调 _on_agenda_advance → done 事件
- 未挂载 EventBus 时静默跳过（不炸）
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest

from src.agents.streamer.agenda.agenda import Agenda, AgendaSegment
from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agenda import AgendaPayload
from src.modules.tools import ToolRegistry


def _make_agenda() -> Agenda:
    return Agenda(
        agenda_id="plan_test_001",
        title="测试节目单",
        segments=[
            AgendaSegment(
                id="seg_1",
                title="开场寒暄",
                task_description="和观众打招呼",
                duration_ms=60000,
            ),
            AgendaSegment(
                id="seg_2",
                title="游戏环节",
                task_description="玩一会儿游戏",
                duration_ms=120000,
            ),
        ],
    )


def _make_agent(event_bus: Optional[EventBus]) -> StreamerAgent:
    from unittest.mock import AsyncMock, MagicMock

    llm = MagicMock()
    llm.chat = AsyncMock()
    prompt = MagicMock()
    config = StreamerAgentConfig(
        proactive_enabled=False,
        agenda_enabled=False,
        profanity_enabled=False,
    )
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=event_bus,
        tool_registry=ToolRegistry(),
    )


def _collect_updates(bus: EventBus) -> List[AgendaPayload]:
    received: List[AgendaPayload] = []

    async def _on_update(event_name: str, payload: AgendaPayload, source: str) -> None:
        received.append(payload)

    bus.on(CoreEvents.AGENDA_UPDATE, _on_update, AgendaPayload)
    return received


async def _drain_tasks() -> None:
    """让 fire-and-forget 的 emit 后台任务跑完。"""
    await asyncio.sleep(0.05)


class TestAgendaUpdateEmission:
    """环节变更 → agenda.update 广播。"""

    @pytest.mark.asyncio
    async def test_skip_emits_done_for_previous_segment(self):
        """skip 后旧环节以 action=done 广播。"""
        bus = EventBus(enable_stats=False)
        received = _collect_updates(bus)
        agent = _make_agent(bus)
        agent._agenda_state.start(_make_agenda())

        success, _msg, _snap = await agent.agenda_control("skip")
        assert success is True
        await _drain_tasks()

        assert len(received) == 1
        payload = received[0]
        assert payload.action == "done"
        assert payload.item.label == "开场寒暄"
        assert payload.item.done is True

    @pytest.mark.asyncio
    async def test_jump_emits_schedule_for_target_segment(self):
        """jump 后目标环节以 action=schedule 广播。"""
        bus = EventBus(enable_stats=False)
        received = _collect_updates(bus)
        agent = _make_agent(bus)
        agent._agenda_state.start(_make_agenda())

        success, _msg, _snap = await agent.agenda_control("jump", segment_id="seg_2")
        assert success is True
        await _drain_tasks()

        assert len(received) == 1
        payload = received[0]
        assert payload.action == "schedule"
        assert payload.item.label == "游戏环节"
        assert payload.item.done is False

    @pytest.mark.asyncio
    async def test_advance_callback_emits_done(self):
        """自动推进回调 → 上一环节 done 事件。"""
        bus = EventBus(enable_stats=False)
        received = _collect_updates(bus)
        agent = _make_agent(bus)
        agent._agenda_state.start(_make_agenda())
        agent._agenda_state.advance_to("seg_2")

        agent._on_agenda_advance("seg_2", "auto")
        await _drain_tasks()

        assert len(received) == 1
        payload = received[0]
        assert payload.action == "done"
        assert payload.item.label == "开场寒暄"

    @pytest.mark.asyncio
    async def test_without_event_bus_is_silent(self):
        """未挂载 EventBus（None）时不广播也不抛异常。"""
        agent = _make_agent(None)
        agent._agenda_state.start(_make_agenda())

        success, _msg, _snap = await agent.agenda_control("skip")
        assert success is True  # 不抛即通过
