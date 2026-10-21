"""主播侧递话消化：提醒队列入队与取空一次制、直播中催醒一轮主动决策、参考块【运营提醒】段"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import make_streamer_agent


def _agent(proactive: bool = True):
    agent = make_streamer_agent({"proactive": {"enabled": proactive}})
    agent._rounds = AsyncMock()
    agent._rounds.execute = AsyncMock(return_value={"success": True})
    return agent


def test_queue_accepts_until_full_and_drains_once() -> None:
    agent = make_streamer_agent()
    assert agent.receive_prompt(content="   ", source="operator") is False
    for i in range(5):
        assert agent.receive_prompt(content=f"提醒 {i}", source="operator") is True
    # 上限 5：满后拒收，必达不允许静默挤掉旧条目
    assert agent.receive_prompt(content="第 6 条", source="operator") is False
    assert list(agent._reminder_queue) == [f"提醒 {i}" for i in range(5)]

    # 决策执行器持有的提醒来源即 Agent 队列：取空即送达
    text = agent._rounds._reminders_provider()
    assert "提醒 0" in text and "提醒 4" in text
    assert len(agent._reminder_queue) == 0 and agent._drain_reminders() == ""


@pytest.mark.parametrize("proactive", [True, False], ids=["proactive_on", "master_switch_off"])
async def test_reminder_wakes_proactive_round_when_live(proactive: bool) -> None:
    """主动发言总开关关闭时仍催醒：必达不辖"自主找话说"的规矩。"""
    agent = _agent(proactive)
    agent._live_active = True
    agent.receive_prompt(content="别忘了解释新版本改动", source="operator")
    await agent._maybe_flush()

    agent._rounds.execute.assert_awaited_once()
    call = agent._rounds.execute.call_args
    assert call.args[0] == []
    assert (call.kwargs["proactive"], call.kwargs["trigger_reason"]) == (True, "proactive:reminder")


@pytest.mark.parametrize("scenario", ["just_spoke", "not_live"])
async def test_reminder_gate_by_scenario(scenario: str) -> None:
    """防接龙窗口内不促发也不消费（队列保留）；未开播递话必达——照常触发消费，不因下播吞掉留言。"""
    agent = _agent()
    if scenario == "just_spoke":
        agent._live_active = True
        agent._room_state.record_speech(now_ms())
    agent.receive_prompt(content="插一句提醒", source="operator")
    await agent._maybe_flush()

    if scenario == "just_spoke":
        agent._rounds.execute.assert_not_awaited()
        assert len(agent._reminder_queue) == 1
        return
    agent._rounds.execute.assert_awaited_once()
    call = agent._rounds.execute.call_args
    assert (call.kwargs["proactive"], call.kwargs["trigger_reason"]) == (True, "proactive:reminder")
    assert len(agent._reminder_queue) == 1


async def test_planner_reference_contains_reminder_section_only_when_present() -> None:
    planner = Planner(
        config={"planner_max_steps": 2},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        context_enabled=False,
    )
    common: dict = {"forced": False, "proactive": False}
    with_reminder = await planner._assemble_reference([], None, None, reminders="- 上线新活动", **common)
    assert "【运营提醒】" in with_reminder and "上线新活动" in with_reminder
    assert "【运营提醒】" not in await planner._assemble_reference([], None, None, **common)
