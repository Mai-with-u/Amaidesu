"""主播侧递话消化（reminder 队列）测试。

覆盖：
- L1：receive_prompt 入提醒队列；满拒（上限 5）/ 空内容拒收；取空一次制
- L2 催醒：直播态提醒非空 → 空缓冲 tick 促发 proactive 决策
  （trigger_reason = "proactive:reminder"）；防接龙窗口内不触发且队列保留
- 三道闸豁免：主动发言总开关关闭时提醒催醒仍生效（必达语义）
- 注入：Planner 参考块出现【运营提醒】段
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.client import LLMResponse
from src.modules.llm.payload import Response
from src.modules.time_utils import now_ms


def _make_agent_config(**overrides: Any) -> StreamerConfig:
    defaults: Dict[str, Any] = {
        "proactive": {"enabled": True},
        "word_filter": {"enabled": False},
        "batch": {
            "batch_window_ms": 100,
            "tick_interval_ms": 50,
        },
    }
    defaults.update(overrides)
    return StreamerConfig.from_dict(defaults)


def _build_streamer_agent(**config_overrides: Any) -> StreamerAgent:
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    return StreamerAgent(
        config=_make_agent_config(**config_overrides),
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=None,
    )


def _spy_rounds(agent: StreamerAgent) -> AsyncMock:
    spy = AsyncMock()
    spy.execute = AsyncMock(return_value={"success": True})
    agent._rounds = spy
    return spy


# ---------------------------------------------------------------------------
# L1：提醒队列行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_prompt_enqueues_with_source():
    agent = _build_streamer_agent()
    assert agent.receive_prompt(content="多讲讲基地建设", source="operator") is True
    assert list(agent._reminder_queue) == ["多讲讲基地建设"]


@pytest.mark.asyncio
async def test_receive_prompt_full_queue_refuses():
    """队列上限 5：满后拒收（必达不允许静默挤掉旧条目）。"""
    agent = _build_streamer_agent()
    for i in range(5):
        assert agent.receive_prompt(content=f"提醒 {i}", source="operator") is True
    assert agent.receive_prompt(content="第 6 条", source="operator") is False
    assert list(agent._reminder_queue) == [f"提醒 {i}" for i in range(5)]


@pytest.mark.asyncio
async def test_receive_prompt_empty_content_refuses():
    agent = _build_streamer_agent()
    assert agent.receive_prompt(content="   ", source="operator") is False
    assert len(agent._reminder_queue) == 0


@pytest.mark.asyncio
async def test_drain_reminders_once():
    """决策窗入口取空队列——送达一次制。"""
    agent = _build_streamer_agent()
    agent.receive_prompt(content="第一条", source="operator")
    agent.receive_prompt(content="第二条", source="operator")

    text = agent._drain_reminders()
    assert "第一条" in text and "第二条" in text
    assert agent._reminder_queue == set() or len(agent._reminder_queue) == 0
    assert agent._drain_reminders() == "", "第二次取空为空（消费即送达）"


# ---------------------------------------------------------------------------
# L2：催醒触发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_wakes_proactive_round_when_live():
    agent = _build_streamer_agent()
    rounds_spy = _spy_rounds(agent)
    agent._live_active = True

    assert agent.receive_prompt(content="别忘了解释新版本改动", source="operator") is True
    await agent._maybe_flush()

    rounds_spy.execute.assert_awaited_once()
    call = rounds_spy.execute.call_args
    assert call.args[0] == []
    assert call.kwargs["proactive"] is True
    assert call.kwargs["trigger_reason"] == "proactive:reminder"


@pytest.mark.asyncio
async def test_executor_reminders_provider_wired_to_agent_queue():
    """决策执行器持有的提醒来源即 Agent 队列（真实决策窗入口取空——送达一次制）。"""
    agent = _build_streamer_agent()
    assert agent._rounds._reminders_provider is not None
    assert agent._rounds._reminders_provider() == ""

    agent.receive_prompt(content="接线验证", source="operator")
    text = agent._rounds._reminders_provider()
    assert "接线验证" in text
    assert len(agent._reminder_queue) == 0


@pytest.mark.asyncio
async def test_reminder_respects_anti_chain_interval_and_keeps_queue():
    """防接龙窗口内不催醒；队列保留到窗口过后自然送达。"""
    agent = _build_streamer_agent()
    rounds_spy = _spy_rounds(agent)
    agent._live_active = True
    agent._room_state.record_speech(now_ms())  # 刚发过言

    assert agent.receive_prompt(content="插一句提醒", source="operator") is True
    await agent._maybe_flush()

    rounds_spy.execute.assert_not_awaited()
    assert len(agent._reminder_queue) == 1, "防接龙阻塞时提醒保留（必达，等下一 tick）"


@pytest.mark.asyncio
async def test_reminder_bypasses_proactive_master_switch():
    """总开关关闭时提醒催醒仍生效（三道闸豁免：必达不辖自主找话说的规矩）。"""
    agent = _build_streamer_agent(proactive={"enabled": False})
    rounds_spy = _spy_rounds(agent)
    agent._live_active = True

    assert agent.receive_prompt(content="总开关关了也要送到", source="operator") is True
    await agent._maybe_flush()

    rounds_spy.execute.assert_awaited_once()
    assert rounds_spy.execute.call_args.kwargs["trigger_reason"] == "proactive:reminder"


@pytest.mark.asyncio
async def test_reminder_not_consumed_when_not_live():
    """未开播：不促发也不消费（与环节变更/待定夺信号同一边界）。"""
    agent = _build_streamer_agent()
    rounds_spy = _spy_rounds(agent)

    assert agent.receive_prompt(content="开播前留言", source="operator") is True
    await agent._maybe_flush()

    rounds_spy.execute.assert_not_awaited()
    assert len(agent._reminder_queue) == 1


# ---------------------------------------------------------------------------
# 注入：Planner 参考块
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_reference_contains_reminder_section():
    """reminders 非空 → 参考块出现【运营提醒】段；为空 → 整段不出现。"""
    planner = Planner(
        config={"planner_max_steps": 2},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        memory=None,
        context_enabled=False,  # 裸路径：仅情境标注与叙事段，便于断言
    )

    with_reminder = await planner._assemble_reference(
        [], None, None, forced=False, proactive=False, game_narrative="", body_narrative="", reminders="- 上线新活动"
    )
    assert "【运营提醒】" in with_reminder
    assert "上线新活动" in with_reminder

    without_reminder = await planner._assemble_reference(
        [], None, None, forced=False, proactive=False, game_narrative="", body_narrative=""
    )
    assert "【运营提醒】" not in without_reminder
