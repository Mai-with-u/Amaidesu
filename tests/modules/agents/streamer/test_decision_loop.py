"""StreamerAgent 决策循环（Planner ReAct + Replyer）：弹幕到发言、静默、主动发言的开播闸、可观测事件与统计口径

LLM mock 按 profile 分流：planner 响应是中立 ToolCall（空 tool_calls = 自然终止 = 静默），
replyer 响应正文即台词（纯文本契约）。
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools import ToolRegistry
from tests.agents.streamer.helpers import make_streamer_agent


def _danmaku(text: str = "主播好可爱") -> RoomMessagePayload:
    return RoomMessagePayload(message_type="danmaku", user=RoomMessageUser(id="u1", name="观众A"), content=text)


def _planner_step(*tool_calls: ToolCall) -> Response:
    return Response(success=True, content="", tool_calls=list(tool_calls))


def _reply_call(target: str = "u1", topic: str = "主播好可爱", call_id: str = "call_p1") -> ToolCall:
    args = {"topic_summary": topic, "reply_guidance": "回应夸奖", "target": target, "is_novel": True}
    return ToolCall(id=call_id, name="streamer_reply", arguments=args)


class _Rig:
    """装配好的 Agent + 总线 + 分流 LLM；planner 响应从队列取，replyer 返回固定响应或失败。"""

    def __init__(self, planner_steps: list | Exception | None = None, replyer: Response | None = None, **sections: Any):
        self.planner_steps = [_planner_step(_reply_call())] if planner_steps is None else planner_steps
        self.replyer = replyer or Response(success=True, content="谢谢支持！")
        self.llm = MagicMock()
        self.llm.generate = AsyncMock(side_effect=self._dispatch)
        self.bus = EventBus()
        self.agent = make_streamer_agent(
            sections or None, llm=self.llm, event_bus=self.bus, tool_registry=ToolRegistry()
        )
        self.decisions: list[PlannerDecisionPayload] = []
        self.stages: list[StreamerStagePayload] = []
        self.decided = asyncio.Event()

        async def on_decision(name, payload, source) -> None:
            self.decisions.append(payload)
            self.decided.set()

        async def on_stage(name, payload, source) -> None:
            self.stages.append(payload)

        self.bus.on(CoreEvents.PLANNER_DECISION, on_decision, model_class=PlannerDecisionPayload)
        self.bus.on(CoreEvents.STREAMER_STAGE, on_stage, model_class=StreamerStagePayload)

    async def _dispatch(self, *args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") == "planner":
            if isinstance(self.planner_steps, Exception):
                raise self.planner_steps
            return self.planner_steps.pop(0)
        return self.replyer

    def calls(self, profile: str) -> list:
        return [c for c in self.llm.generate.await_args_list if c.kwargs.get("profile") == profile]

    async def emit(self, text: str = "主播好可爱") -> None:
        await self.bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _danmaku(text), source="bilibili")

    async def wait_decision(self) -> PlannerDecisionPayload:
        await asyncio.wait_for(self.decided.wait(), timeout=3)
        return self.decisions[0]


@pytest.fixture
async def rig_factory():
    rigs: list[_Rig] = []

    async def _make(*args: Any, **kwargs: Any) -> _Rig:
        rig = _Rig(*args, **kwargs)
        await rig.agent.start()
        rigs.append(rig)
        return rig

    yield _make
    for rig in rigs:
        await rig.agent.cleanup()
        await rig.bus.cleanup()


async def test_danmaku_flows_through_planner_reply_and_replyer(rig_factory) -> None:
    rig = await rig_factory()
    spec_names = {spec.name for spec in rig.agent.list_tools()}
    registered = {spec.full_name for spec in rig.agent._tool_registry.list_tools()}
    # 只有真工具进声明；proactive/command 是代码直连的内部件，不注册
    assert spec_names == {"reply"} and "streamer_reply" in registered
    assert not {"should_speak_proactively", "parse_command"} & registered

    await rig.emit("主播好可爱！")
    decision = await rig.wait_decision()

    assert len(rig.calls("planner")) >= 1 and len(rig.calls("replyer")) >= 1
    stats = rig.agent.get_statistics()
    assert stats["total_messages"] >= 1 and stats["total_replies"] >= 1
    assert decision.should_reply is True and decision.error is None and decision.silent_reason is None
    assert decision.round_id.startswith("rnd_") and decision.utterance_id.startswith("utt_")
    assert [b.user_name for b in decision.batch] == ["观众A"]


async def test_natural_termination_is_silence_without_replyer(rig_factory) -> None:
    rig = await rig_factory([_planner_step()])
    await rig.emit("test")
    await rig.wait_decision()
    assert len(rig.calls("planner")) == 1 and len(rig.calls("replyer")) == 0
    assert rig.agent.get_statistics()["total_no_action"] >= 1


async def test_decision_events_pair_stages_and_planner_failure_is_still_reported(rig_factory) -> None:
    rig = await rig_factory()
    await rig.emit()
    decision = await rig.wait_decision()
    await asyncio.sleep(0.1)
    assert len(rig.decisions) == 1  # 每轮决策恰好一条 planner.decision
    assert [s.stage for s in rig.stages] == ["planning", "idle"]  # 阶段事件成对
    assert (rig.stages[0].agent_state, rig.stages[1].agent_state) == ("running", "wait")
    assert rig.stages[0].round_id == decision.round_id

    failing = await rig_factory(RuntimeError("boom"))
    await failing.emit("hi")
    failed = await failing.wait_decision()  # 失败可见性是核心价值：失败也必须发决策事件
    assert failed.should_reply is False and "llm_error" in failed.error


async def test_reply_target_flows_into_decision_event(rig_factory) -> None:
    rig = await rig_factory([_planner_step(_reply_call(target="msg_abc", call_id="call_r2"))])
    await rig.emit()
    assert (await rig.wait_decision()).reply_to_message_id == "msg_abc"


async def test_proactive_is_gated_by_live_session_not_by_danmaku(rig_factory) -> None:
    """开播前主动发言静默且 pending 保留，开播后首个 tick 消费；下播收闸，下场前 pending 保留。"""
    rig = await rig_factory([_planner_step()], proactive={"enabled": True})
    agent = rig.agent
    agent._proactive_signals.set_rundown()
    await asyncio.sleep(0.2)
    assert agent.get_statistics()["total_proactive"] == 0 and agent._proactive_signals.rundown_pending is True

    await rig.bus.emit(
        CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, source="manual", title="测试场"), source="test"
    )
    await asyncio.sleep(0.2)
    assert agent._live_active is True and agent.get_statistics()["total_proactive"] >= 1
    assert agent._proactive_signals.rundown_pending is False

    await rig.bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=1, reason="手动结束"), source="test")
    await asyncio.sleep(0.1)
    assert agent._live_active is False
    agent._proactive_signals.set_rundown()
    baseline = agent.get_statistics()["total_proactive"]
    await asyncio.sleep(0.3)
    assert agent.get_statistics()["total_proactive"] == baseline and agent._proactive_signals.rundown_pending is True


async def test_rundown_pending_survives_disabled_switch_and_fires_after_reenable(rig_factory) -> None:
    """总开关关闭期间发生的环节切换信号保留，重开开关后下一 tick 补说开场白。"""
    rig = await rig_factory([_planner_step()], proactive={"enabled": False})
    agent = rig.agent
    await rig.bus.emit(
        CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, source="manual", title="测试场"), source="test"
    )
    agent._proactive_signals.set_rundown()
    await asyncio.sleep(0.2)
    # 开关关闭期间信号不被静默消费
    assert agent.get_statistics()["total_proactive"] == 0 and agent._proactive_signals.rundown_pending is True

    agent._proactive_trigger.set_enabled(True)
    await asyncio.sleep(0.2)
    assert agent._proactive_signals.rundown_pending is False
    assert agent.get_statistics()["total_proactive"] >= 1


async def test_rundown_pending_survives_reminder_preemption(rig_factory) -> None:
    """同一 tick 被更高优先级的运营递话抢占时环节切换信号保留，队列清空后下一 tick 触发。

    决策执行器替换为空转桩，使递话不被送达消费（真实送达在任何决策窗取空队列），
    借此把"被抢占的 tick"稳定放大成可观察窗口。
    """
    rig = await rig_factory([_planner_step()] * 8, proactive={"enabled": True})
    agent = rig.agent
    await rig.bus.emit(
        CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, source="manual", title="测试场"), source="test"
    )
    agent._proactive_signals.set_rundown()
    agent._reminder_queue.append("后台留言")
    real_execute = agent._execute_round
    agent._execute_round = AsyncMock()  # 递话滞留队列，reminder 每 tick 都抢先
    await asyncio.sleep(0.15)
    # reminder 抢占每个 tick，rundown 信号保留待送达窗口让位之后
    assert agent.get_statistics()["total_proactive"] >= 1 and agent._proactive_signals.rundown_pending is True

    agent._execute_round = real_execute
    agent._reminder_queue.clear()
    await asyncio.sleep(0.15)
    assert agent._proactive_signals.rundown_pending is False
    assert agent.get_statistics()["total_proactive"] >= 2


async def test_danmaku_reply_is_not_gated_by_live_session(rig_factory) -> None:
    rig = await rig_factory()
    assert rig.agent._live_active is False
    await rig.emit()
    await rig.wait_decision()
    assert len(rig.calls("planner")) >= 1


async def test_handle_message_direct_entry_buffers_without_event_bus() -> None:
    agent = make_streamer_agent(tool_registry=ToolRegistry())
    await agent.handle_message(_danmaku("直接调用"))
    assert agent.get_statistics()["total_messages"] == 1


# -------------------- 场次生命周期礼节：收播道别 + 下播后递话必达 --------------------


async def _wait_decisions(rig: _Rig, count: int, timeout: float = 3.0) -> list:
    """等决策事件累计到 count 条（收播道别经总线后台任务异步到达，需轮询）。"""

    async def _poll() -> None:
        while len(rig.decisions) < count:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout)
    return rig.decisions


async def test_signoff_fires_on_manual_live_end(rig_factory) -> None:
    """真收播（场次结束、Agent 仍运行）触发一轮 forced 决策窗，reason 为 signoff。"""
    rig = await rig_factory([_planner_step(_reply_call())], proactive={"enabled": False})
    await rig.bus.emit(
        CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=1, source="manual", title="测试场"), source="test"
    )
    await asyncio.sleep(0.1)
    assert rig.decisions == []  # 开播本身不触发决策窗

    await rig.bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=1, reason="手动结束"), source="test")
    decisions = await _wait_decisions(rig, 1)
    assert len(decisions) == 1
    assert decisions[0].trigger_reason == "proactive:signoff"
    assert decisions[0].forced is True


async def test_signoff_not_fired_on_stopped_agent(rig_factory) -> None:
    """应用停机路径的收播（Agent 已停）不触发道别：进程退出时道别无意义。"""
    rig = await rig_factory([_planner_step(_reply_call())])
    await rig.agent.stop()
    baseline = len(rig.calls("planner"))
    await rig.bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=1, reason="进程退出"), source="test")
    await asyncio.sleep(0.2)
    assert rig.agent._live_active is False
    assert len(rig.calls("planner")) == baseline and rig.decisions == []


async def test_reminder_reaches_planner_after_live_end(rig_factory) -> None:
    """下播后运营递话仍送达：豁免开播闸触发决策窗并取空队列（递话必达）。"""
    rig = await rig_factory([_planner_step(_reply_call())], proactive={"enabled": True})
    agent = rig.agent
    assert agent._live_active is False
    agent._reminder_queue.append("准备下播，和观众说再见")
    await asyncio.sleep(0.3)

    assert agent._reminder_queue == deque(), "递话队列必须在决策窗内被取空"
    decisions = await _wait_decisions(rig, 1)
    assert decisions[0].trigger_reason == "proactive:reminder"
    # 递话文本进入 planner 参考段（【运营提醒】块）
    planner_messages = [c.args[0] for c in rig.calls("planner")]
    assert any("准备下播，和观众说再见" in str(m) for m in planner_messages)


async def test_no_proactive_without_live_and_without_reminder(rig_factory) -> None:
    """未开播且无递话维持静默：早退不触发决策、不消费 pending 信号（现状不回归）。"""
    rig = await rig_factory([_planner_step()], proactive={"enabled": True})
    agent = rig.agent
    agent._proactive_signals.set_rundown()
    agent._proactive_signals.set_external()
    await asyncio.sleep(0.3)
    assert rig.decisions == []
    assert agent._proactive_signals.rundown_pending is True and agent._proactive_signals.external_pending is True


# 统计字段口径


async def test_reply_duration_is_measured_on_successful_reply(rig_factory) -> None:
    rig = await rig_factory()
    inner = rig.llm.generate.side_effect

    async def slow_replyer(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("profile") == "replyer":
            await asyncio.sleep(0.05)  # Windows time.time 粒度粗，注入延迟保证实测 > 0
        return await inner(*args, **kwargs)

    rig.llm.generate = AsyncMock(side_effect=slow_replyer)
    result = await rig.agent.debug_test_decision(batch=[{"user": "观众A", "text": "主播好可爱"}])
    assert result["speech"] == "谢谢支持！" and result["reply_duration_ms"] > 0
    assert rig.agent.get_statistics()["replyer_failures"] == 0


async def test_replyer_failures_count_without_touching_planner_failures(rig_factory) -> None:
    """reply 工具失败（Replyer LLM 失败）→ 记 replyer_failures；总轮数 = 回复 + 无动作，口径自洽。"""
    failing = Response(success=False, content=None, error="llm down")
    steps = [_planner_step(_reply_call(topic="t")), _planner_step(_reply_call(topic="t"))] + [_planner_step()] * 3
    rig = await rig_factory(steps, replyer=failing)

    results = [await rig.agent.debug_test_decision(batch=[{"user": "观众A", "text": t}]) for t in ("hi", "hi2")]

    assert all(r["speech"] is None and r["reply_duration_ms"] >= 0 for r in results)
    stats = rig.agent.get_statistics()
    assert (stats["replyer_failures"], stats["planner_failures"]) == (2, 0)
    assert (stats["total_replies"], stats["total_no_action"]) == (0, 2)
