"""长施工挂起期连续静默提醒升级与长任务完工更新笔记提示的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.agents.minecraft.lead_mode import (
    LEAD_MODE_PROMPT_SECTION,
    PROLONGED_SILENCE_MS,
    PROLONGED_SILENCE_NUDGE_COUNT,
    SILENCE_NUDGE_PREFIX,
    format_silence_nudge_notice,
)
from src.modules.agents import (
    AgentManager,
    CommentaryAck,
    CommentaryRequest,
    LeadCoordinator,
    LeadHandoverContext,
    build_agent_control_provider,
)
from src.modules.events.event_bus import EventBus
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from tests.agents.minecraft._wiring_helpers import StreamerPlaceholder
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)


@pytest.mark.parametrize(
    ("silent_ms", "consecutive_count", "elapsed_ms", "task_summary", "expect_prolonged"),
    [
        (75_000, 1, 0, "建造营地木屋（砌东墙）", False),
        (75_000, PROLONGED_SILENCE_NUDGE_COUNT, 75_000, "建造营地木屋（砌东墙）", True),
        (PROLONGED_SILENCE_MS, 1, 0, "采掘铁矿", True),
        (75_000, 1, PROLONGED_SILENCE_MS, "", True),
    ],
    ids=["first-gentle", "second-consecutive", "long-silent-ms", "elapsed-since-first"],
)
def test_format_silence_nudge_notice_distinguishes_gentle_and_prolonged(
    silent_ms: int,
    consecutive_count: int,
    elapsed_ms: int,
    task_summary: str,
    expect_prolonged: bool,
) -> None:
    """首次温和提醒保留让出选项；连续多次或久静达阈时取消消极放行句并附当前任务摘要。"""
    notice = format_silence_nudge_notice(
        silent_ms=silent_ms,
        consecutive_count=consecutive_count,
        elapsed_since_first_ms=elapsed_ms,
        active_task_summary=task_summary,
    )
    assert notice.startswith(SILENCE_NUDGE_PREFIX)
    assert "framework_commentary" in notice
    if task_summary:
        assert task_summary in notice
    if expect_prolonged:
        assert "不必硬聊" not in notice
        assert "直接结束本轮等待" not in notice
        assert "避免长时间冷场" in notice
    else:
        assert "不必硬聊" in notice
        assert "直接结束本轮等待即可" in notice


async def test_consecutive_silence_nudges_escalate_with_task_summary_and_reset_after_speaking(
    fake_clock: Any,
) -> None:
    """挂起期第 1 次提醒未开口后，第 2 次升级为久静提醒并带当前目标摘要；开口或收到回声后重置计数。"""
    server = FakeMaicraft()
    commentaries: List[CommentaryRequest] = []
    step = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        step[0] += 1
        if step[0] == 1:
            return _resp(
                "发起长期施工目标",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "build", "purpose": "建造主屋"}},
                        "c_build",
                    )
                ],
            )
        if step[0] == 2:
            return _resp("施工中，先静默等待")
        if step[0] == 3:
            # 收到第 1 次温和静默提醒：故意不开口，直接结束本轮
            return _resp("先不打扰，继续等施工")
        if step[0] == 4:
            # 收到第 2 次久静提醒：主动开口提交解说
            return _resp(
                "向观众同步施工近况",
                calls=[
                    _call(
                        "framework_commentary",
                        {"summary": "主屋墙体正在顺利推进中，稍后准备封顶", "urgency": "normal"},
                        "c_comm",
                    )
                ],
            )
        # 开口后再收到新的静默提醒：应已重置回第 1 次温和提醒
        return _resp("收到新一轮提醒后继续等待")

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    manager = AgentManager()
    manager.register(StreamerPlaceholder())
    coordinator = LeadCoordinator(manager, event_bus=bus, clock=fake_clock.now_ms)

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        mcp_client=SimpleNamespace(connected=True),
        clock=lambda: fake_clock.now_ms() / 1000.0,
        lead_coordinator=coordinator,
        lead_idle_wakeup_s=0.0,
        config=offline_config(events_wait_ms=1000, commentary_guard_window_ms=0),
    )
    manager.register(agent)
    control_provider = build_agent_control_provider(
        manager,
        ledger,
        event_bus=bus,
        clock=fake_clock.now_ms,
        lead_coordinator=coordinator,
    )
    registry.register_provider(control_provider)

    async def _on_commentary(req: CommentaryRequest) -> CommentaryAck:
        commentaries.append(req)
        return CommentaryAck(accepted=True, spoken=True, reason="spoken")

    coordinator.bind_commentary_handler(_on_commentary)

    try:
        assert (
            await coordinator.request_game_lead(LeadHandoverContext(segment_title="营地建设"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: step[0] >= 2 and len(agent.tracked_goal_ids) == 1 and not agent.batch_active)

        # 第 1 次静默提醒（75s）：应为温和提醒，含当前进行中目标摘要与让出提示
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 3 and not agent.batch_active)
        first_nudge_text = str(llm.seen[2][-1].get("content") or "")
        assert SILENCE_NUDGE_PREFIX in first_nudge_text
        assert "建造主屋" in first_nudge_text
        assert "不必硬聊" in first_nudge_text

        # 第 2 次静默提醒（又过 75s，期间未开口）：升级为久静提醒，取消消极放行句并带当前目标摘要
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 4 and len(commentaries) == 1 and not agent.batch_active)
        second_nudge_text = str(llm.seen[3][-1].get("content") or "")
        assert SILENCE_NUDGE_PREFIX in second_nudge_text
        assert "建造主屋" in second_nudge_text
        assert "连续第 2 次提醒" in second_nudge_text
        assert "不必硬聊" not in second_nudge_text
        assert "直接结束本轮等待" not in second_nudge_text

        # 模型已通过 framework_commentary 开口：计数器应清零，下一次 75s 提醒回到温和文案
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 5 and not agent.batch_active)
        after_speak_text = str(llm.seen[4][-1].get("content") or "")
        assert SILENCE_NUDGE_PREFIX in after_speak_text
        assert "不必硬聊" in after_speak_text
        assert "连续第 2 次提醒" not in after_speak_text

        # 再次验证：若主播侧经 receive_spoken_echo 实际发言，同样清零连续静默计数
        agent.receive_spoken_echo("主播刚回复完弹幕啦", source="streamer")
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 6 and not agent.batch_active)
        after_echo_text = str(llm.seen[5][-1].get("content") or "")
        assert "不必硬聊" in after_echo_text
        assert "连续第 2 次提醒" not in after_echo_text
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_silence_nudge_streak_not_reset_when_commentary_accepted_but_not_spoken(
    fake_clock: Any,
) -> None:
    """framework_commentary 返回 accepted=True 但 spoken=False（被去重/过滤/未送达）时不得清零静默计数。"""
    server = FakeMaicraft()
    commentaries: List[CommentaryRequest] = []
    step = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        del messages
        step[0] += 1
        if step[0] == 1:
            return _resp(
                "发起施工目标",
                calls=[_call("maicraft_execute", {"goal": {"ability": "build", "purpose": "修围墙"}}, "c_build")],
            )
        if step[0] == 2:
            return _resp("先等施工")
        if step[0] == 3:
            # 收到第 1 次静默提醒后尝试提交解说，但主播侧将返回 accepted=True, spoken=False
            return _resp(
                "尝试暖场解说",
                calls=[
                    _call(
                        "framework_commentary",
                        {"summary": "围墙正在稳步推进", "urgency": "normal"},
                        "c_comm_suppressed",
                    )
                ],
            )
        return _resp("收到后续提醒后继续等待")

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    manager = AgentManager()
    manager.register(StreamerPlaceholder())
    coordinator = LeadCoordinator(manager, event_bus=bus, clock=fake_clock.now_ms)

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        mcp_client=SimpleNamespace(connected=True),
        clock=lambda: fake_clock.now_ms() / 1000.0,
        lead_coordinator=coordinator,
        lead_idle_wakeup_s=0.0,
        config=offline_config(events_wait_ms=1000, commentary_guard_window_ms=0),
    )
    manager.register(agent)
    control_provider = build_agent_control_provider(
        manager,
        ledger,
        event_bus=bus,
        clock=fake_clock.now_ms,
        lead_coordinator=coordinator,
    )
    registry.register_provider(control_provider)

    async def _on_commentary_suppressed(req: CommentaryRequest) -> CommentaryAck:
        commentaries.append(req)
        return CommentaryAck(accepted=True, spoken=False, reason="word_filtered")

    coordinator.bind_commentary_handler(_on_commentary_suppressed)

    try:
        assert (
            await coordinator.request_game_lead(LeadHandoverContext(segment_title="修筑围墙"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: step[0] >= 2 and len(agent.tracked_goal_ids) == 1 and not agent.batch_active)

        # 第 1 次静默提醒：模型调 framework_commentary 但主播侧未实际出声（spoken=False）
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 3 and len(commentaries) == 1 and not agent.batch_active)

        # 75 秒后再次静默提醒：因为上次并未实际出声，计数不得被清零，必须升级为连续第 2 次久静提醒
        fake_clock.tick(75_000)
        agent.receive_silence_nudge(silent_ms=75_000)
        await _wait_until(lambda: step[0] >= 4 and not agent.batch_active)
        second_nudge_text = str(llm.seen[3][-1].get("content") or "")
        assert SILENCE_NUDGE_PREFIX in second_nudge_text
        assert "修围墙" in second_nudge_text
        assert "连续第 2 次提醒" in second_nudge_text
        assert "不必硬聊" not in second_nudge_text
    finally:
        await agent.stop()
        await bus.cleanup()


def test_minecraft_agent_prompt_mentions_refreshing_notebook_after_long_task() -> None:
    """提示词在工作笔记条款中正向提示长时施工或阶段性大任务完工收尾时及时刷新笔记。"""
    reset_prompt_manager()
    try:
        rendered = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "长时施工或阶段性大任务完工与收尾时，及时刷新笔记" in rendered
    assert "避免笔记长期滞后于现状" in rendered
    assert "持续久静" in LEAD_MODE_PROMPT_SECTION
