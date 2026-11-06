"""主播侧递话消化：提醒队列入队与取空一次制、直播中催醒一轮主动决策、参考块【运营提醒】段"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import PlannerConfig, Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.trigger_cause import TriggerKind
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent
from tests.config.helpers import wait_until


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
    assert (
        call.kwargs["proactive"],
        call.kwargs["trigger_cause"].kind,
    ) == (True, TriggerKind.REMINDER)


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
    assert (
        call.kwargs["proactive"],
        call.kwargs["trigger_cause"].kind,
    ) == (True, TriggerKind.REMINDER)
    assert len(agent._reminder_queue) == 1


async def test_planner_reference_contains_reminder_section_only_when_present() -> None:
    planner = Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        context_enabled=False,
    )
    common: dict = {"forced": False, "proactive": False}
    with_reminder, _ = await planner._assemble_reference([], None, None, reminders="- 上线新活动", **common)
    assert "【运营提醒】" in with_reminder and "上线新活动" in with_reminder
    without_reminder, _ = await planner._assemble_reference([], None, None, **common)
    assert "【运营提醒】" not in without_reminder


async def test_reminder_round_immediate_while_batch_lingering() -> None:
    """批次滞留期投递递话：下一轮 flush 评估（近即时）执行递话轮，批次原地不动。"""
    agent = _agent()
    agent._live_active = True
    # 弹幕批次滞留：未到放行条件（消息入缓冲但不开窗）
    await agent.handle_message(
        RoomMessagePayload(
            message_type="danmaku",
            user=RoomMessageUser(id="u1", name="观众"),
            content="还在攒批",
            timestamp_ms=now_ms(),
        )
    )
    # 防接龙间隔先立住：递话轮只认"过间隔"的催醒
    agent._room_state.record_speech(now_ms())
    agent.receive_prompt(content="批次没消化也先送这条", source="operator")
    await agent._maybe_flush()

    agent._rounds.execute.assert_not_awaited()  # 刚说过话，防接龙窗口内不促发
    assert len(agent._reminder_queue) == 1, "队列保留待下一 tick 重试"

    # 过防接龙间隔（回拨最后发言时刻）后的下一 tick：递话轮立即执行
    agent._room_state.record_speech(now_ms() - 10 * 60 * 1000)
    await agent._maybe_flush()
    agent._rounds.execute.assert_awaited_once()
    call = agent._rounds.execute.call_args
    assert call.args[0] == [], "递话轮不带弹幕批次（批次仍在缓冲里原地等待）"
    assert (call.kwargs["proactive"], call.kwargs["trigger_cause"].kind) == (True, TriggerKind.REMINDER)


async def test_flush_loop_wakes_on_kick_without_waiting_full_tick() -> None:
    """kick 唤醒位：置位后 flush 循环近即时醒来评估，不再等满一个 tick 间隔。"""
    agent = make_streamer_agent({"proactive": {"enabled": True}, "batch": {"tick_interval_ms": 60_000}})
    agent._rounds = AsyncMock()
    agent._rounds.execute = AsyncMock(return_value={"success": True})
    agent.receive_prompt(content="尽快送达", source="operator")
    agent._running = True
    agent._flush_task = asyncio.create_task(agent._flush_loop())
    try:
        await wait_until(lambda: agent._rounds.execute.await_count >= 1, what="kick 唤醒的递话轮执行")
    finally:
        agent._running = False
        if agent._flush_task is not None:
            agent._flush_task.cancel()
            try:
                await agent._flush_task
            except asyncio.CancelledError:
                pass
    call = agent._rounds.execute.call_args
    assert call.args[0] == []
    assert (call.kwargs["proactive"], call.kwargs["trigger_cause"].kind) == (True, TriggerKind.REMINDER)


@pytest.mark.parametrize(
    ("anti_chain_blocks", "batch_due", "expected_rounds"),
    [
        (True, True, ["batch"]),
        (True, False, []),
        (False, False, ["reminder"]),
    ],
    ids=["blocked_batch_due", "blocked_no_batch", "not_blocked"],
)
async def test_pending_reminder_does_not_starve_due_batch(
    anti_chain_blocks: bool, batch_due: bool, expected_rounds: list[str]
) -> None:
    """有递话时一个 tick 的走向：递话被防接龙挡住时到期的弹幕批次照常成轮，递话随那一轮送出。

    - 挡住 + 弹幕批次已到期：同一个 tick 里弹幕那一轮执行，开窗取走递话（不再陪防接龙干等）
    - 挡住 + 没有弹幕：什么都不做，递话留在队列等下一 tick
    - 没挡住：递话那一轮主动发言执行
    """
    executor = FakeRoundExecutor()
    executor.drain_reminders = True  # 真实执行器在任何决策窗入口都取空提醒队列
    # 单批上限 1 条：一条弹幕进缓冲即到期（batch_full），不靠真实时间流逝
    agent = make_streamer_agent({"batch": {"batch_max_size": 1}}, round_executor=executor)
    agent._live_active = True
    if batch_due:
        await agent.handle_message(
            RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u1", name="观众"),
                content="主播看这里",
                timestamp_ms=now_ms(),
            )
        )
    if anti_chain_blocks:
        agent._room_state.record_speech(now_ms())  # 她刚开过口，防接龙间隔内
    agent.receive_prompt(content="提醒一下今晚的抽奖", source="operator")
    await agent._maybe_flush()

    kinds = ["batch" if call["batch"] else "reminder" for call in executor.calls]
    assert kinds == expected_rounds
    if not expected_rounds:
        assert "提醒一下今晚的抽奖" in agent._drain_reminders(), "被挡住的递话留在队列等下一 tick"
        return
    assert "提醒一下今晚的抽奖" in executor.window_reminders[0], "递话随本 tick 执行的那一轮送出"
    if expected_rounds == ["reminder"]:
        assert executor.calls[0]["trigger_cause"].kind is TriggerKind.REMINDER


async def test_no_reminder_flush_loop_polls_as_before() -> None:
    """无递话时行为与现状一致：tick 到点才评估，未开播且队列空不触发任何决策。"""
    agent = make_streamer_agent({"proactive": {"enabled": True}, "batch": {"tick_interval_ms": 50}})
    agent._rounds = AsyncMock()
    agent._rounds.execute = AsyncMock(return_value={"success": True})
    agent._running = True
    agent._flush_task = asyncio.create_task(agent._flush_loop())
    await asyncio.sleep(0.15)
    agent._running = False
    if agent._flush_task is not None:
        agent._flush_task.cancel()
        try:
            await agent._flush_task
        except asyncio.CancelledError:
            pass
    agent._rounds.execute.assert_not_awaited()  # 未开播且无递话：轮询照旧，零决策
