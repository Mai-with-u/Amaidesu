"""动态主从框架核心（LeadCoordinator + BaseAgent 协议 + framework 工具隔离与门禁）测试。

覆盖：
- BaseAgent 默认动态主从协议（兼容既有非自主 Agent）；
- AgentLeadChangedPayload 事件注册与 EventBus 派发；
- LeadCoordinator 候选发现、交权/收权/断连回收/人工覆盖、互斥与幂等；
- 跨 Agent 无耦合桥接（解说素材、发声回声、非强杀观众建议）；
- AgentControlProvider 工具可见名单隔离、guide_for 切换与委派/结单门禁；
- AgentFactory 的可选 lead_coordinator 自动注入探测。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest

from src.modules.agents import (
    AgentControl,
    AgentManager,
    BaseAgent,
    CommentaryAck,
    CommentaryRequest,
    LeadCoordinator,
    LeadHandoverContext,
    SuggestAttachment,
    build_agent_control_provider,
)
from src.modules.agents.deps import AgentDeps
from src.modules.agents.factory import _accepts_init_kwarg
from src.modules.dashboard.services.component_runtime import (
    ComponentRuntimeContext,
    restart_component,
    start_instance,
)
from src.modules.events import EventBus, register_core_events
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentLeadChangedPayload
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from src.modules.types.emotion_vocab import Emotion


class _PlainAgent(BaseAgent):
    """未开启自主领航的普通 Agent（验证默认协议兼容）。"""

    name = "plain_game"
    description = "普通委派型游戏"

    def __init__(self, name: str = "plain_game", *, accept_prompt: bool = False) -> None:
        self.__class__ = type(f"_PlainAgent_{name}", (type(self),), {"name": name, "description": f"普通游戏 {name}"})
        super().__init__(heartbeat_interval_ms=0)
        self._accept_prompt = accept_prompt
        self.prompts: List[tuple[str, str]] = []
        self.delegations: List[tuple[str, str]] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        self.delegations.append((task_id, instruction))
        return None

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        if not self._accept_prompt:
            return False
        self.prompts.append((content, source))
        return True


class _StreamerStub(BaseAgent):
    """主播 Agent 最小桩（不接收委派、不支持自主领航）。"""

    name = "streamer"
    description = "测试主播"

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


class _AutonomousGameAgent(BaseAgent):
    """支持自主领航的可控测试游戏 Agent（无真实游戏包依赖）。"""

    name = "auto_game"
    description = "自主游戏代理"
    supports_autonomous_lead = True

    def __init__(
        self,
        name: str = "auto_game",
        *,
        ready: bool = True,
        accept_lead: bool = True,
        accept_suggestion: bool = True,
        yield_summary: str = "刚把铁矿炼好回基地",
        raise_on_enter: bool = False,
        raise_on_yield: bool = False,
        raise_on_ready: bool = False,
        raise_on_echo: bool = False,
        supports_delegation: bool = True,
    ) -> None:
        attrs: dict[str, Any] = {
            "name": name,
            "description": f"自主游戏 {name}",
            "supports_autonomous_lead": True,
        }
        if not supports_delegation:
            attrs["receive_delegation"] = BaseAgent.receive_delegation
        self.__class__ = type(f"_AutonomousGameAgent_{name}", (type(self),), attrs)
        super().__init__(heartbeat_interval_ms=0)
        self.ready = ready
        self.accept_lead = accept_lead
        self.accept_suggestion = accept_suggestion
        self.yield_summary = yield_summary
        self.raise_on_enter = raise_on_enter
        self.raise_on_yield = raise_on_yield
        self.raise_on_ready = raise_on_ready
        self.raise_on_echo = raise_on_echo

        self.entered_contexts: List[LeadHandoverContext] = []
        self.yield_reasons: List[str] = []
        self.spoken_echoes: List[tuple[str, str, int]] = []
        self.rundown_updates: List[tuple[str, str, str, int]] = []
        self.raise_on_rundown_update: bool = False
        self.silence_nudges: List[int] = []
        self.raise_on_silence_nudge: bool = False
        self.suggestions: List[tuple[str, str, Optional[List[str]]]] = []
        self.delegations: List[tuple[str, str]] = []

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        self.delegations.append((task_id, instruction))
        return None

    def can_take_lead(self) -> bool:
        if self.raise_on_ready:
            raise RuntimeError("ready probe boom")
        return self.ready

    async def enter_lead(self, context: LeadHandoverContext) -> bool:
        if self.raise_on_enter:
            raise RuntimeError("enter boom")
        if not self.accept_lead:
            return False
        self.entered_contexts.append(context)
        return True

    async def yield_lead(self, *, reason: str = "") -> str:
        self.yield_reasons.append(reason)
        if self.raise_on_yield:
            raise RuntimeError("yield boom")
        return self.yield_summary

    def receive_spoken_echo(self, text: str, *, source: str = "", timestamp_ms: int = 0) -> None:
        if self.raise_on_echo:
            raise RuntimeError("echo boom")
        self.spoken_echoes.append((text, source, timestamp_ms))

    def receive_rundown_update(
        self,
        *,
        segment_id: str = "",
        segment_title: str = "",
        segment_goal: str = "",
        expected_ms: int = 0,
    ) -> None:
        if self.raise_on_rundown_update:
            raise RuntimeError("rundown boom")
        self.rundown_updates.append((segment_id, segment_title, segment_goal, expected_ms))

    def receive_silence_nudge(self, *, silent_ms: int) -> None:
        if self.raise_on_silence_nudge:
            raise RuntimeError("nudge boom")
        self.silence_nudges.append(silent_ms)

    async def receive_suggestion(
        self,
        content: str,
        *,
        source: str = "streamer",
        source_dialogue: Optional[List[str]] = None,
    ) -> bool:
        if not self.accept_suggestion:
            return False
        self.suggestions.append((content, source, source_dialogue))
        return True


async def test_base_agent_default_lead_protocol_and_suggestion_fallback() -> None:
    """默认 BaseAgent 不支持自主领航；receive_suggestion 回退到 receive_prompt。"""
    plain = _PlainAgent("plain_a", accept_prompt=False)
    assert plain.supports_autonomous_lead is False
    assert plain.can_take_lead() is False
    assert await plain.enter_lead(LeadHandoverContext(segment_title="测试")) is False
    assert await plain.yield_lead(reason="end") == ""
    plain.receive_spoken_echo("观众好", source="streamer", timestamp_ms=1000)
    assert await plain.receive_suggestion("试试左边") is False

    promptable = _PlainAgent("plain_b", accept_prompt=True)
    assert (
        await promptable.receive_suggestion("去看看河边", source="streamer", source_dialogue=["观众: 去河边"]) is True
    )
    assert promptable.prompts == [("去看看河边", "streamer")]


async def test_lead_lifecycle_events_idempotency_and_listeners(fake_clock: Any) -> None:
    """验证交权、幂等、收权、断连回收以及事件广播与监听器回调。"""
    register_core_events()
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    streamer = _StreamerStub()
    game = _AutonomousGameAgent("sandbox_game")
    mgr.register(streamer)
    mgr.register(game)
    await mgr.start_all()

    coordinator = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)

    bus_events: List[AgentLeadChangedPayload] = []
    listener_events: List[AgentLeadChangedPayload] = []

    async def _on_bus(name: str, payload: AgentLeadChangedPayload, source: str) -> None:
        assert name == CoreEvents.AGENT_LEAD_CHANGED and source == "LeadCoordinator"
        bus_events.append(payload)

    def _sync_listener(payload: AgentLeadChangedPayload) -> None:
        listener_events.append(payload)

    bus.on(CoreEvents.AGENT_LEAD_CHANGED, _on_bus, model_class=AgentLeadChangedPayload)
    coordinator.bind_lead_changed_listener(_sync_listener)
    coordinator.bind_lead_changed_listener(_sync_listener)  # 去重

    # 初始态为主播主控
    snap0 = coordinator.snapshot()
    assert snap0.mode == "streamer"
    assert snap0.lead_agent == "streamer"
    assert snap0.candidates == ["sandbox_game"]
    assert snap0.ready_candidates == ["sandbox_game"]
    assert coordinator.is_game_lead is False

    # 交权给唯一就绪候选
    fake_clock.tick(5_000)
    ctx = LeadHandoverContext(
        segment_id="seg_1", segment_title="冒险环节", segment_goal="探索山洞", expected_ms=600_000
    )
    chosen = await coordinator.request_game_lead(ctx, reason="rundown_game_segment")
    await bus.wait_until_drained()

    assert chosen == "sandbox_game"
    assert coordinator.mode == "game"
    assert coordinator.active_lead_agent == "sandbox_game"
    assert coordinator.is_agent_leading("sandbox_game") is True
    assert len(game.entered_contexts) == 1
    assert game.entered_contexts[0].reason == "rundown_game_segment"
    assert len(bus_events) == 1 and len(listener_events) == 1
    assert bus_events[0].lead_agent == "sandbox_game"
    assert bus_events[0].previous_lead == "streamer"
    assert bus_events[0].mode == "game"
    assert bus_events[0].timestamp_ms == fake_clock.now_ms()
    # 交权事件携带简报正文快照，字段与传入的 LeadHandoverContext 对齐
    brief = bus_events[0].handover_brief
    assert brief is not None
    assert brief.segment_id == "seg_1"
    assert brief.segment_title == "冒险环节"
    assert brief.segment_goal == "探索山洞"
    assert brief.expected_ms == 600_000
    assert brief.save_name == "" and brief.memory_brief == ""
    assert brief.operator_notes == [] and brief.recent_speech == []

    # 同目标重复交权幂等，不重发事件、不重复调 enter_lead
    assert await coordinator.request_game_lead(ctx, target="sandbox_game") == "sandbox_game"
    await bus.wait_until_drained()
    assert len(game.entered_contexts) == 1
    assert len(bus_events) == 1

    # 收回主播主控并返回游戏收尾摘要
    fake_clock.tick(10_000)
    summary = await coordinator.reclaim_streamer_lead(reason="segment_ended")
    await bus.wait_until_drained()
    assert summary == "刚把铁矿炼好回基地"
    assert coordinator.mode == "streamer"
    assert coordinator.active_lead_agent is None
    assert len(bus_events) == 2
    assert bus_events[-1].lead_agent == "streamer"
    assert bus_events[-1].previous_lead == "sandbox_game"
    assert bus_events[-1].handover_summary == "刚把铁矿炼好回基地"
    # 收回方向不带交权简报（收权摘要只走 handover_summary）
    assert bus_events[-1].handover_brief is None

    # 主播态重复收权幂等
    assert await coordinator.reclaim_streamer_lead(reason="again") == ""
    assert len(bus_events) == 2

    # 再次交权后模拟游戏断连：自动安全交回主播主控
    await coordinator.request_game_lead(ctx, target="sandbox_game", reason="resume_play")
    assert await coordinator.notify_agent_unavailable("other_game") == ""  # 非当前主控幂等忽略
    assert coordinator.is_agent_leading("sandbox_game") is True

    recovered_summary = await coordinator.notify_agent_unavailable("sandbox_game", reason="mcp_disconnected")
    await bus.wait_until_drained()
    assert recovered_summary == "刚把铁矿炼好回基地"
    assert coordinator.mode == "streamer"
    assert bus_events[-1].reason == "mcp_disconnected"

    coordinator.unbind_lead_changed_listener(_sync_listener)
    await mgr.stop_all()
    await bus.cleanup()


async def test_candidate_discovery_multigame_and_error_isolation(fake_clock: Any) -> None:
    """验证就绪探测、禁用态、多候选不盲选以及 enter_lead/yield_lead 异常隔离。"""
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game_a = _AutonomousGameAgent("game_a", ready=True, yield_summary="A 收尾")
    game_b = _AutonomousGameAgent("game_b", ready=True, accept_lead=False)
    broken = _AutonomousGameAgent("broken_game", raise_on_ready=True)
    mgr.register(game_a)
    mgr.register(game_b)
    mgr.register(broken)
    await mgr.start_all()

    # 禁用态拒绝一切交权
    disabled = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms, enabled=False)
    assert await disabled.request_game_lead(LeadHandoverContext()) is None
    with pytest.raises(RuntimeError, match="已禁用"):
        await disabled.set_override("game_a")

    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    assert coord.lead_capable_agents() == ["game_a", "game_b", "broken_game"]
    assert coord.ready_lead_agents() == ["game_a", "game_b"]  # broken_game 探测异常被隔离视为未就绪

    # 多候选且未指定 target：不盲选
    assert await coord.request_game_lead(LeadHandoverContext()) is None

    # 显式指定 game_a 成功，随后切换到拒收的 game_b：旧主控先收权，新主控拒收后安全回落主播并广播
    events: List[AgentLeadChangedPayload] = []
    coord.bind_lead_changed_listener(lambda p: events.append(p))
    assert await coord.request_game_lead(LeadHandoverContext(), target="game_a") == "game_a"
    assert await coord.request_game_lead(LeadHandoverContext(), target="game_b") is None
    assert coord.mode == "streamer"
    assert events[-1].lead_agent == "streamer"
    assert events[-1].previous_lead == "game_a"
    assert events[-1].handover_summary == "A 收尾"
    # 拒收回落的 fallback 事件带原简报（描述原本要交什么，供复盘）
    assert events[-1].handover_brief is not None

    # 目标 yield_lead 抛异常也不阻断收权
    game_a.raise_on_yield = True
    assert await coord.request_game_lead(LeadHandoverContext(), target="game_a") == "game_a"
    assert await coord.reclaim_streamer_lead(reason="force_back") == ""
    assert coord.mode == "streamer"
    assert events[-1].handover_brief is None  # 主动收回方向的简报为 None

    await mgr.stop_all()
    await bus.cleanup()


async def test_set_override_modes_and_handover_builder(fake_clock: Any) -> None:
    """验证人工覆盖 auto / streamer / 具体候选名与交接简报构建器。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("world_game")
    plain = _PlainAgent("plain_game")
    mgr.register(game)
    mgr.register(plain)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)

    async def _builder(target: str) -> LeadHandoverContext:
        return LeadHandoverContext(segment_title=f"人工切给 {target}", memory_brief="仓库在东边")

    coord.bind_handover_builder(_builder)

    # 非法目标（空串、未知、非自主 Agent）一律 ValueError
    for bad in ("", "ghost", "plain_game"):
        with pytest.raises(ValueError, match="无效的主控切换目标"):
            await coord.set_override(bad)

    # 手动切到就绪游戏 → 走 builder 生成交接简报并立即进入游戏主控
    snap = await coord.set_override("world_game", reason="ops_click")
    assert snap.mode == "game" and snap.lead_agent == "world_game" and snap.override == "world_game"
    assert game.entered_contexts[-1].memory_brief == "仓库在东边"

    # 手动切到 streamer → 立即收权，且随后自动交权请求被 override='streamer' 拦下
    snap2 = await coord.set_override("streamer", reason="ops_takeover")
    assert snap2.mode == "streamer" and snap2.override == "streamer"
    assert await coord.request_game_lead(LeadHandoverContext()) is None

    # 恢复 auto → 再次允许自动交权
    snap3 = await coord.set_override("auto", reason="ops_auto")
    assert snap3.override == "auto" and snap3.mode == "streamer"
    assert await coord.request_game_lead(LeadHandoverContext()) == "world_game"

    # 目标未就绪或拒收时抛 RuntimeError，且不污染 override
    await coord.reclaim_streamer_lead()
    game.ready = False
    with pytest.raises(RuntimeError, match="未就绪"):
        await coord.set_override("world_game")
    assert coord.override == "auto"

    game.ready = True
    game.accept_lead = False
    with pytest.raises(RuntimeError, match="拒绝接管"):
        await coord.set_override("world_game")
    assert coord.override == "auto"

    await mgr.stop_all()


async def test_cross_agent_bridges_commentary_echo_and_suggestion(fake_clock: Any) -> None:
    """验证解说通道、发声回声（同步零唤醒）与非强杀观众建议桥接。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("block_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)

    # 主播主控态下：解说被拒、发声回声跳过、建议被拒
    req = CommentaryRequest(game="block_game", summary="挖到第一颗钻石")
    ack_idle = await coord.submit_commentary("block_game", req)
    assert ack_idle.accepted is False and ack_idle.reason == "not_in_lead"

    coord.publish_spoken_echo("还没开始玩呢", source="streamer")
    assert game.spoken_echoes == []

    sug_idle = await coord.send_suggestion("去抓羊")
    assert sug_idle["accepted"] is False and sug_idle["reason"] == "not_in_game_lead"

    # 进入游戏主控态
    await coord.request_game_lead(LeadHandoverContext(), target="block_game")

    # 未绑定解说处理器时返回明确拒绝
    ack_no_handler = await coord.submit_commentary("block_game", req)
    assert ack_no_handler.accepted is False and ack_no_handler.reason == "no_commentary_handler"

    # 绑定主播解说处理器后受理并补齐时间戳
    received_reqs: List[CommentaryRequest] = []

    async def _handler(r: CommentaryRequest) -> CommentaryAck:
        received_reqs.append(r)
        return CommentaryAck(accepted=True, spoken=True, reason="ok")

    coord.bind_commentary_handler(_handler)
    fake_clock.tick(1234)
    ack_ok = await coord.submit_commentary("block_game", req)
    assert ack_ok == CommentaryAck(accepted=True, spoken=True, reason="ok")
    assert received_reqs[0].timestamp_ms == fake_clock.now_ms()

    # 非主控方冒名提交被拒
    ack_imposter = await coord.submit_commentary("streamer", req)
    assert ack_imposter.accepted is False and ack_imposter.reason == "not_in_lead"

    # 发声回声同步转发且异常隔离
    coord.publish_spoken_echo("哇真的挖到钻石了！", source="streamer")
    assert game.spoken_echoes == [("哇真的挖到钻石了！", "streamer", fake_clock.now_ms())]
    game.raise_on_echo = True
    coord.publish_spoken_echo("不会抛异常", source="streamer")  # 异常隔离

    # 非强杀建议送达主控游戏
    sug_ok = await coord.send_suggestion("观众想看搭个小桥", source="streamer", source_dialogue=["弹幕: 搭桥"])
    assert sug_ok == {"accepted": True, "target": "block_game", "lead_agent": "block_game", "reason": ""}
    assert game.suggestions == [("观众想看搭个小桥", "streamer", ["弹幕: 搭桥"])]

    # 指定错误目标被拒
    sug_wrong = await coord.send_suggestion("试试", target="other_game")
    assert sug_wrong["accepted"] is False and sug_wrong["reason"] == "target_not_leading"

    await mgr.stop_all()


async def test_framework_tools_visibility_guide_and_delegation_guard(fake_clock: Any) -> None:
    """验证工具可见名单隔离、guide_for 动态切换以及游戏主控态委派/结单门禁。"""
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    streamer = _StreamerStub()
    game = _AutonomousGameAgent("solo_game")
    mgr.register(streamer)
    mgr.register(game)
    await mgr.start_all()

    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    registry = ToolRegistry(event_bus=bus)
    provider = build_agent_control_provider(mgr, ledger, event_bus=bus, clock=fake_clock.now_ms, lead_coordinator=coord)
    control = AgentControl(mgr, ledger, event_bus=bus, lead_coordinator=coord)
    assert control.lead_coordinator is coord
    assert registry.register_provider(provider) == 6

    # 主播主控态：streamer 永远看不见 framework_commentary，能看到委派四件与原委派说明
    streamer_tools_before = {s.full_name for s in registry.list_tools(for_agent="streamer")}
    assert "framework_commentary" not in streamer_tools_before
    assert {
        "framework_delegate",
        "framework_prompt",
        "framework_task_status",
        "framework_task_settle",
    } <= streamer_tools_before
    assert "framework_delegate" in provider.guide_for("streamer")
    assert "when_busy" in provider.guide_for("streamer")

    # 非主控态：solo_game 看不见 framework_commentary，直调也被业务拒绝且不熔断
    assert registry.list_tools(for_agent="solo_game") == []
    denied_comm = await registry.invoke(
        ToolInvocation(
            tool_name="framework_commentary",
            arguments={"summary": "现在还不是我主控"},
            source="solo_game-react",
            invoking_agent="solo_game",
        )
    )
    assert denied_comm.success is False and denied_comm.failure_kind == "business"
    assert denied_comm.structured_content["reason"] == "not_in_lead"

    # 主播主控态下调用 framework_suggest 返回清晰拒绝
    denied_sug = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "去海边看看"},
            source="streamer",
            invoking_agent="streamer",
        )
    )
    assert denied_sug.success is False and denied_sug.failure_kind == "business"
    assert denied_sug.structured_content["reason"] == "not_in_game_lead"

    # 切入游戏主控态
    received_commentary: List[CommentaryRequest] = []

    async def _on_commentary(req: CommentaryRequest) -> CommentaryAck:
        received_commentary.append(req)
        return CommentaryAck(accepted=True, spoken=True, reason="spoken")

    coord.bind_commentary_handler(_on_commentary)
    assert (
        await coord.request_game_lead(LeadHandoverContext(segment_title="自由探索"), target="solo_game") == "solo_game"
    )

    # 游戏主控态下：solo_game 动态看到 framework_commentary（且看不到委派和 suggest）；streamer 永远看不到 commentary
    game_tools = {s.full_name for s in registry.list_tools(for_agent="solo_game")}
    assert game_tools == {"framework_commentary"}
    streamer_tools_lead = {s.full_name for s in registry.list_tools(for_agent="streamer")}
    assert "framework_commentary" not in streamer_tools_lead
    assert "framework_suggest" in streamer_tools_lead

    # guide_for("streamer") 不再输出委派强杀守则，改为游戏主控态观众建议说明
    streamer_guide = provider.guide_for("streamer")
    assert "framework_suggest" in streamer_guide
    assert "when_busy" not in streamer_guide
    assert "framework_task_settle" not in streamer_guide

    # solo_game 调 framework_commentary 成功
    ok_comm = await registry.invoke(
        ToolInvocation(
            tool_name="framework_commentary",
            arguments={"summary": "发现一座村庄", "mood": "excited", "urgency": "high", "about_player": "小羊"},
            source="solo_game-react",
            invoking_agent="solo_game",
        )
    )
    assert ok_comm.success is True
    assert ok_comm.structured_content == {"accepted": True, "spoken": True, "reason": "spoken"}
    assert received_commentary[0].game == "solo_game"
    assert received_commentary[0].urgency == "high"
    assert received_commentary[0].about_player == "小羊"

    # streamer 调 framework_suggest 成功，且不建委派账本、不触发 receive_delegation
    ok_sug = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "观众说可以跟村民换点面包"},
            source="planner-react",
            invoking_agent="streamer",
        )
    )
    assert ok_sug.success is True
    assert ok_sug.structured_content == {
        "accepted": True,
        "target": "solo_game",
        "lead_agent": "solo_game",
        "reason": "",
    }
    assert len(ledger) == 0 and game.delegations == []
    assert game.suggestions[-1][0] == "观众说可以跟村民换点面包"

    # streamer 意外调 framework_delegate / framework_task_settle 指向正在主控的游戏 → 结构化业务拒绝且不熔断
    for _ in range(4):
        blocked_del = await registry.invoke(
            ToolInvocation(
                tool_name="framework_delegate",
                arguments={"instruction": "强行改去挖矿"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert blocked_del.success is False
        assert blocked_del.failure_kind == "business"
        assert blocked_del.structured_content["error"] == "game_in_lead"
        assert blocked_del.structured_content["suggested_tool"] == "framework_suggest"
    assert registry.is_tripped("framework_delegate") is False
    assert len(ledger) == 0 and game.delegations == []

    blocked_settle = await registry.invoke(
        ToolInvocation(
            tool_name="framework_task_settle",
            arguments={"agent": "solo_game", "item": "在做", "outcome": "drop"},
            source="planner-react",
            invoking_agent="streamer",
        )
    )
    assert blocked_settle.success is False
    assert blocked_settle.failure_kind == "business"
    assert blocked_settle.structured_content["error"] == "game_in_lead"

    # 并发交权与收权串行互斥：不破坏状态机不变量
    await asyncio.gather(
        coord.reclaim_streamer_lead(reason="race_1"),
        coord.request_game_lead(LeadHandoverContext(), target="solo_game", reason="race_2"),
    )
    assert (coord.mode == "streamer" and coord.active_lead_agent is None) or (
        coord.mode == "game" and coord.active_lead_agent == "solo_game"
    )

    await mgr.stop_all()
    await bus.cleanup()


async def test_streamer_only_mode_hides_lead_tools_and_keeps_compat(fake_clock: Any) -> None:
    """streamer_only 模式下不注册动态主从工具，对主播 0 暴露、完全兼容旧行为。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    registry = ToolRegistry()
    provider = build_agent_control_provider(mgr, lead_coordinator=coord)
    assert registry.register_provider(provider) == 4
    assert registry.list_tools(for_agent="streamer") == []
    assert provider.guide_for("streamer") == ""

    await mgr.stop_all()


def test_accepts_init_kwarg_detection() -> None:
    """验证工厂的可选构造参数探测对显式形参、**kwargs 与未声明类均准确。"""

    class _WithLead:
        def __init__(self, *, lead_coordinator: Optional[Any] = None) -> None:
            self.lead_coordinator = lead_coordinator

    class _WithKwargs:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _WithoutLead:
        def __init__(self, name: str = "") -> None:
            self.name = name

    assert _accepts_init_kwarg(_WithLead, "lead_coordinator") is True
    assert _accepts_init_kwarg(_WithKwargs, "lead_coordinator") is True
    assert _accepts_init_kwarg(_WithoutLead, "lead_coordinator") is False


async def test_set_enabled_updates_snapshot_and_gates_handover(fake_clock: Any) -> None:
    """set_enabled 实时同步 enabled 状态并生效于交权门禁与快照。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    mgr.register(_AutonomousGameAgent("toggle_game"))
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms, enabled=True)
    assert coord.enabled is True and coord.snapshot().enabled is True

    coord.set_enabled(False)
    assert coord.enabled is False and coord.snapshot().enabled is False
    assert await coord.request_game_lead(LeadHandoverContext()) is None
    with pytest.raises(RuntimeError, match="已禁用"):
        await coord.set_override("toggle_game")

    coord.set_enabled(True)
    assert coord.enabled is True and coord.snapshot().enabled is True
    assert await coord.request_game_lead(LeadHandoverContext()) == "toggle_game"

    await mgr.stop_all()


async def test_dynamic_enable_rebuild_and_runtime_context_preserve_lead_coordinator(
    monkeypatch: pytest.MonkeyPatch, fake_clock: Any
) -> None:
    """动态启用、rebuild 与 ComponentRuntimeContext 热重启均保留组合根同一个 LeadCoordinator。"""
    seen_deps: List[tuple[str, AgentDeps]] = []

    def _fake_instantiate(name: str, config: Optional[dict[str, Any]], *, deps: AgentDeps) -> Optional[BaseAgent]:
        del config
        seen_deps.append((name, deps))
        if name == "streamer":
            return _StreamerStub()
        if name == "dyn_game":
            return _AutonomousGameAgent("dyn_game")
        return None

    monkeypatch.setattr("src.modules.agents.factory.instantiate_agent", _fake_instantiate)

    registry = ToolRegistry()
    mgr = AgentManager(tool_registry=registry)
    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    mgr.set_lead_coordinator(coord)
    assert mgr.lead_coordinator is coord

    # 1. AgentManager.enable_agent 不显式传 lead_coordinator 时回退成员默认
    assert await mgr.enable_agent("streamer", {}, deps=AgentDeps()) is True
    assert seen_deps[-1][1].lead_coordinator is coord

    # 2. AgentManager.rebuild 重建后依然拿到同一单例
    assert await mgr.rebuild("streamer") is True
    assert seen_deps[-1][1].lead_coordinator is coord

    # 3. 显式传入的 lead_coordinator 可覆盖成员默认
    other_coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    assert await mgr.enable_agent("dyn_game", {}, deps=AgentDeps(lead_coordinator=other_coord)) is True
    assert seen_deps[-1][1].lead_coordinator is other_coord
    assert await mgr.disable_agent("dyn_game") is True

    # 4. Dashboard ComponentRuntimeContext.from_server -> start_instance / restart_component 透传同一单例
    fake_server = SimpleNamespace(
        collector_manager=None,
        agent_manager=mgr,
        event_bus=None,
        llm_manager=None,
        prompt_manager=None,
        task_tracker=None,
        config_service=None,
        lead_coordinator=coord,
    )
    ctx = ComponentRuntimeContext.from_server(fake_server)
    assert ctx.lead_coordinator is coord

    start_res = await start_instance(ctx, "agents", "dyn_game", {})
    assert start_res is not None and start_res.success is True and start_res.created is True
    assert seen_deps[-1][1].lead_coordinator is coord

    restart_res = await restart_component(ctx, "agents", "streamer", {})
    assert restart_res.success is True and restart_res.skipped is False
    assert seen_deps[-1][1].lead_coordinator is coord

    await mgr.stop_all()


async def test_dynamic_enable_and_disable_autonomous_agent_refreshes_framework_tools(
    monkeypatch: pytest.MonkeyPatch, fake_clock: Any
) -> None:
    """启动期仅启用主播时无候选不注册主从工具；动态启用自主游戏后自动刷新注册且可调用，停用后自动摘除。"""

    def _fake_instantiate(name: str, config: Optional[dict[str, Any]], *, deps: AgentDeps) -> Optional[BaseAgent]:
        del config, deps
        if name == "late_game":
            return _AutonomousGameAgent("late_game")
        return None

    monkeypatch.setattr("src.modules.agents.factory.instantiate_agent", _fake_instantiate)

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    mgr = AgentManager(tool_registry=registry)
    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    mgr.set_lead_coordinator(coord)

    # 启动期只有主播：保持"无候选时不暴露主从两工具"的契约
    mgr.register(_StreamerStub())
    provider = build_agent_control_provider(mgr, ledger, event_bus=bus, clock=fake_clock.now_ms, lead_coordinator=coord)
    assert registry.register_provider(provider) == 4
    assert not registry.has("framework_commentary")
    assert not registry.has("framework_suggest")
    assert registry.list_tools(for_agent="streamer") == []

    # 运行期动态启用自主游戏 Agent：AgentManager.register 自动刷新 framework 工具集
    assert await mgr.enable_agent("late_game", {}) is True
    assert registry.has("framework_commentary")
    assert registry.has("framework_suggest")
    assert registry.visible_to_of("framework_commentary") == ["late_game"]
    assert registry.visible_to_of("framework_suggest") == ["streamer"]

    # 切入游戏主控态：游戏可看到并调用 framework_commentary，主播可看到并调用 framework_suggest
    received_comm: List[CommentaryRequest] = []

    async def _on_comm(req: CommentaryRequest) -> CommentaryAck:
        received_comm.append(req)
        return CommentaryAck(accepted=True, spoken=True, reason="dispatched")

    coord.bind_commentary_handler(_on_comm)
    assert (
        await coord.request_game_lead(LeadHandoverContext(segment_title="动态开玩"), target="late_game") == "late_game"
    )

    assert {s.full_name for s in registry.list_tools(for_agent="late_game")} == {"framework_commentary"}
    assert "framework_suggest" in {s.full_name for s in registry.list_tools(for_agent="streamer")}

    comm_res = await registry.invoke(
        ToolInvocation(
            tool_name="framework_commentary",
            arguments={"summary": "动态启用后发现矿洞"},
            source="late_game-react",
            invoking_agent="late_game",
        )
    )
    assert comm_res.success is True and len(received_comm) == 1

    sug_res = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "观众想看搭火把"},
            source="planner-react",
            invoking_agent="streamer",
        )
    )
    assert sug_res.success is True and sug_res.structured_content["target"] == "late_game"

    # 动态停用自主游戏后：先收回主控，unregister 自动刷新摘除主从两工具，恢复无候选契约
    await coord.reclaim_streamer_lead(reason="before_disable")
    assert await mgr.disable_agent("late_game") is True
    assert not registry.has("framework_commentary")
    assert not registry.has("framework_suggest")
    assert registry.list_tools(for_agent="streamer") == []

    await mgr.stop_all()
    await bus.cleanup()


async def test_ready_listener_binding_and_exception_isolation(fake_clock: Any) -> None:
    """ready_listener 支持同步/异步回调、重复注册去重、注销与异常隔离，且不直接修改主控态。"""
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("ready_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    seen: List[tuple[str, str]] = []

    def _sync_listener(agent_name: str, reason: str) -> None:
        seen.append((f"sync:{agent_name}", reason))

    async def _async_listener(agent_name: str, reason: str) -> None:
        seen.append((f"async:{agent_name}", reason))

    async def _boom_listener(agent_name: str, reason: str) -> None:
        del agent_name, reason
        raise RuntimeError("ready listener boom")

    coord.bind_ready_listener(_sync_listener)
    coord.bind_ready_listener(_sync_listener)  # 去重
    coord.bind_ready_listener(_boom_listener)
    coord.bind_ready_listener(_async_listener)

    await coord.notify_agent_ready("ready_game", reason="mcp_reconnected")
    # 不直接抢权：主控态保持原样，交由监听方决策
    assert coord.mode == "streamer"
    assert seen == [("sync:ready_game", "mcp_reconnected"), ("async:ready_game", "mcp_reconnected")]

    # 注销后不再触发（重复注销幂等）
    coord.unbind_ready_listener(_sync_listener)
    coord.unbind_ready_listener(_sync_listener)
    seen.clear()
    await coord.notify_agent_ready("ready_game")
    assert seen == [("async:ready_game", "agent_ready")]

    # 空白名称忽略
    seen.clear()
    await coord.notify_agent_ready("   ")
    assert seen == []

    await mgr.stop_all()


async def test_framework_suggest_forwards_source_dialogue_from_attachment_and_args(fake_clock: Any) -> None:
    """framework_suggest 优先从宿主 attachment 提取并规范化 source_dialogue，兼容 args 兜底且不污染工具 Schema。"""
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("suggest_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    registry = ToolRegistry(event_bus=bus)
    provider = build_agent_control_provider(mgr, event_bus=bus, clock=fake_clock.now_ms, lead_coordinator=coord)
    registry.register_provider(provider)

    # 1) LLM 工具 JSON Schema 不包含 source_dialogue 字段
    suggest_spec = next(spec for spec in provider.list_tools() if spec.full_name == "framework_suggest")
    assert "source_dialogue" not in (suggest_spec.parameters_schema or {}).get("properties", {})

    assert (
        await coord.request_game_lead(LeadHandoverContext(segment_title="探险"), target="suggest_game")
        == "suggest_game"
    )

    # 2) SuggestAttachment 优先透传（即便 args 里也有 source_dialogue，以宿主 attachment 为准）
    res1 = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "去北边遗迹看看", "source_dialogue": ["不应被优先使用的 args"]},
            source="planner-react",
            invoking_agent="streamer",
            attachment=SuggestAttachment(source_dialogue=["  小星: 去 (128, 64, -240) 挖下界合金块  ", ""]),
        )
    )
    assert res1.success is True
    assert game.suggestions[-1] == (
        "去北边遗迹看看",
        "streamer",
        ["小星: 去 (128, 64, -240) 挖下界合金块"],
    )

    # 3) 兼容映射形态 attachment（如 dict 含 message 对象或纯字符串）
    res2 = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "顺路带点圆石"},
            source="planner-react",
            invoking_agent="streamer",
            attachment={"source_dialogue": [{"role": "user", "content": "观众A: 带两组圆石"}]},  # type: ignore[arg-type]
        )
    )
    assert res2.success is True
    assert game.suggestions[-1] == ("顺路带点圆石", "streamer", ["观众A: 带两组圆石"])

    # 4) 未带 attachment 时兼容读取 args['source_dialogue']
    res3 = await registry.invoke(
        ToolInvocation(
            tool_name="framework_suggest",
            arguments={"content": "记得插火把", "source_dialogue": ["观众B: 矿洞太黑了"]},
            source="planner-react",
            invoking_agent="streamer",
        )
    )
    assert res3.success is True
    assert game.suggestions[-1] == ("记得插火把", "streamer", ["观众B: 矿洞太黑了"])

    await mgr.stop_all()
    await bus.cleanup()


async def test_publish_rundown_update_forwards_only_in_game_lead_and_isolates_errors(fake_clock: Any) -> None:
    """publish_rundown_update 仅在游戏主控态同步当前环节给主控游戏 Agent，并隔离异常；BaseAgent 默认空实现。"""
    plain = _PlainAgent("plain_rundown")
    assert (
        plain.receive_rundown_update(
            segment_id="seg_0",
            segment_title="闲聊",
            segment_goal="随便聊聊",
            expected_ms=60_000,
        )
        is None
    )

    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("rundown_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    # 主播主控态下调用静默跳过
    coord.publish_rundown_update(segment_id="s1", segment_title="开场", segment_goal="聊两句", expected_ms=300_000)
    assert game.rundown_updates == []

    # 切入游戏主控后转发环节更新
    assert await coord.request_game_lead(LeadHandoverContext(segment_id="s1"), target="rundown_game") == "rundown_game"
    coord.publish_rundown_update(
        segment_id="s2",
        segment_title="主线推进",
        segment_goal="探索并建造仓库",
        expected_ms=1_800_000,
    )
    assert game.rundown_updates == [("s2", "主线推进", "探索并建造仓库", 1_800_000)]

    # 游戏侧抛出异常被隔离，不反噬流程单链路
    game.raise_on_rundown_update = True
    coord.publish_rundown_update(segment_id="s3", segment_title="收尾", segment_goal="整理背包", expected_ms=300_000)
    await mgr.stop_all()


async def test_publish_silence_nudge_forwards_only_in_game_lead_and_isolates_errors(fake_clock: Any) -> None:
    """publish_silence_nudge 仅在游戏主控态投递给当前主控游戏并隔离异常；BaseAgent 默认空操作。"""
    plain = _PlainAgent("plain_nudge")
    assert plain.receive_silence_nudge(silent_ms=80_000) is None

    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("nudge_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    # 主播主控态下返回 False 不投递
    assert coord.publish_silence_nudge(silent_ms=80_000) is False
    assert game.silence_nudges == []

    # 游戏主控态下正常投递并返回 True
    assert await coord.request_game_lead(LeadHandoverContext(segment_id="play"), target="nudge_game") == "nudge_game"
    assert coord.publish_silence_nudge(silent_ms=76_500) is True
    assert game.silence_nudges == [76_500]

    # 异常隔离返回 False，不拖垮主循环
    game.raise_on_silence_nudge = True
    assert coord.publish_silence_nudge(silent_ms=90_000) is False
    await mgr.stop_all()


async def test_commentary_spec_aligns_mood_vocab_and_passes_correction_flag(fake_clock: Any) -> None:
    """framework_commentary 的 mood 枚举与全局 Emotion 词表对齐，且支持可选布尔 correction 透传。"""
    bus = EventBus(enable_stats=False)
    mgr = AgentManager()
    mgr.register(_StreamerStub())
    game = _AutonomousGameAgent("commentary_game")
    mgr.register(game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, event_bus=bus, clock=fake_clock.now_ms)
    received: List[CommentaryRequest] = []

    async def _handler(req: CommentaryRequest) -> CommentaryAck:
        received.append(req)
        return CommentaryAck(accepted=True, spoken=True, reason="dispatched")

    coord.bind_commentary_handler(_handler)
    assert (
        await coord.request_game_lead(LeadHandoverContext(segment_id="main"), target="commentary_game")
        == "commentary_game"
    )

    registry = ToolRegistry(event_bus=bus)
    provider = build_agent_control_provider(mgr, event_bus=bus, clock=fake_clock.now_ms, lead_coordinator=coord)
    registry.register_provider(provider)

    spec = next(item for item in provider.list_tools() if item.full_name == "framework_commentary")
    props = (spec.parameters_schema or {}).get("properties", {})
    assert props["mood"]["enum"] == [emotion.value for emotion in Emotion]
    assert props["correction"]["type"] == "boolean"
    assert "correction" not in (spec.parameters_schema or {}).get("required", [])

    # 默认省略 correction -> False
    res_default = await registry.invoke(
        ToolInvocation(
            tool_name="framework_commentary",
            arguments={"summary": "刚把箱子放好", "mood": "happy"},
            source="commentary_game-react",
            invoking_agent="commentary_game",
        )
    )
    assert res_default.success is True
    assert received[-1].correction is False

    # 显式传 correction=True -> 透传至 CommentaryRequest
    res_fix = await registry.invoke(
        ToolInvocation(
            tool_name="framework_commentary",
            arguments={"summary": "刚才说错啦，那是铁矿不是钻石", "mood": "embarrassed", "correction": True},
            source="commentary_game-react",
            invoking_agent="commentary_game",
        )
    )
    assert res_fix.success is True
    assert received[-1].correction is True
    assert received[-1].mood == "embarrassed"

    await mgr.stop_all()
    await bus.cleanup()


async def test_short_term_memory_and_passive_cue_bridges_isolate_errors(fake_clock: Any) -> None:
    """LeadCoordinator 短期记忆聚合与跨场被动线索桥接：零硬编码游戏名、支持多 Agent 聚合与异常隔离。"""
    plain = _PlainAgent("plain_stm")
    assert await plain.query_short_term_memory("水车") == ()

    class _MemoryGameAgent(_AutonomousGameAgent):
        def __init__(self, name: str, entries: List[Dict[str, Any]], *, boom: bool = False) -> None:
            super().__init__(name)
            self._entries = entries
            self._boom = boom
            self.queries: List[tuple[str, str, int]] = []

        async def query_short_term_memory(
            self, query: str, *, scope: str = "all", limit: int = 6
        ) -> List[Dict[str, Any]]:
            self.queries.append((query, scope, limit))
            if self._boom:
                raise RuntimeError("short term boom")
            return list(self._entries[:limit])

    mgr = AgentManager()
    mgr.register(_StreamerStub())
    broken_game = _MemoryGameAgent("alpha_game", [], boom=True)
    active_game = _MemoryGameAgent(
        "beta_game",
        [{"text": "同伴说别动熔炉", "timestamp_ms": 12345, "source_label": "游戏公屏", "private": False}],
    )
    mgr.register(broken_game)
    mgr.register(active_game)
    await mgr.start_all()

    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)
    # 即使尚未切入游戏主控态，也能按领航候选名册聚合短期记忆并隔离单个 Agent 异常
    items_before = await coord.query_active_short_term_memory("熔炉", scope="this_stream", limit=4)
    assert len(items_before) == 1
    assert items_before[0]["text"] == "同伴说别动熔炉"

    # 切入 beta_game 主控后优先查询主控 Agent
    assert await coord.request_game_lead(LeadHandoverContext(segment_id="s1"), target="beta_game") == "beta_game"
    items_lead = await coord.query_active_short_term_memory("熔炉", scope="all", limit=1)
    assert len(items_lead) == 1
    assert active_game.queries[-1] == ("熔炉", "all", 1)

    # 被动线索未注册回调时安全返回空列表
    assert await coord.fetch_passive_cues(game="beta_game", texts=("熔炉",)) == []

    # 注册回调后透传参数、去重并遵守 limit 上限
    seen_calls: List[Dict[str, Any]] = []

    async def _cue_cb(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        arrived_places: Any = (),
        limit: int = 2,
    ) -> List[str]:
        seen_calls.append(
            {
                "game": game,
                "texts": texts,
                "players": players,
                "situation": situation,
                "arrived_places": arrived_places,
                "limit": limit,
            }
        )
        return ["线索甲", "线索甲", "线索乙", "线索丙"]

    coord.set_passive_cue_provider(_cue_cb)
    cues = await coord.fetch_passive_cues(
        game="beta_game",
        texts=("提到水车",),
        players=[("beta_game", "u1")],
        arrived_places=("北岸水车房",),
        limit=2,
    )
    assert cues == ["线索甲", "线索乙"]
    assert seen_calls[-1]["game"] == "beta_game"
    assert seen_calls[-1]["players"] == (("beta_game", "u1"),)
    assert seen_calls[-1]["arrived_places"] == ("北岸水车房",)

    # 回调抛异常被安全隔离返回空列表
    async def _boom_cb(**_kwargs: Any) -> List[str]:
        raise RuntimeError("cue boom")

    coord.set_passive_cue_provider(_boom_cb)
    assert await coord.fetch_passive_cues(game="beta_game", texts=("提到水车",)) == []

    await mgr.stop_all()


async def test_factory_injects_embedding_setup_into_minecraft_agent() -> None:
    """instantiate_agent('minecraft') 将可选 embedding_setup 透传给 MinecraftAgent，缺失时安全降级。"""
    from src.modules.agents.factory import instantiate_agent
    from src.modules.llm.embedding import EmbeddingSetup

    fake_setup = EmbeddingSetup(client=None, note="test-fallback")
    agent = instantiate_agent(
        "minecraft",
        {"mcp": {"enabled": False}},
        deps=AgentDeps(embedding_setup=fake_setup),
    )
    assert agent is not None
    assert agent.name == "minecraft"
    assert list(await agent.query_short_term_memory("水车")) == []


async def test_publish_rundown_nudge_throttles_per_segment_and_resets_on_update(fake_clock: Any) -> None:
    """publish_rundown_nudge 仅在游戏主控态生效，同环节 5 分钟内节流不重复推送，切段或更新后立即放行。"""

    class _NudgeGameAgent(BaseAgent):
        name = "solo_game"
        description = "测流程单超时推送"
        supports_autonomous_lead = True

        def __init__(self) -> None:
            super().__init__()
            self.nudges: List[Dict[str, Any]] = []

        def list_tools(self) -> Iterable[ToolSpec]:
            return []

        def can_take_lead(self) -> bool:
            return True

        async def enter_lead(self, context: LeadHandoverContext) -> bool:
            del context
            return True

        async def yield_lead(self, *, reason: str = "") -> str:
            del reason
            return ""

        def receive_rundown_nudge(
            self,
            *,
            segment_id: str = "",
            segment_title: str = "",
            elapsed_ms: int = 0,
            expected_ms: int = 0,
            next_segment_id: str = "",
            next_segment_title: str = "",
        ) -> None:
            self.nudges.append(
                {
                    "segment_id": segment_id,
                    "segment_title": segment_title,
                    "elapsed_ms": elapsed_ms,
                    "expected_ms": expected_ms,
                    "next_segment_id": next_segment_id,
                    "next_segment_title": next_segment_title,
                }
            )

    mgr = AgentManager()
    game = _NudgeGameAgent()
    mgr.register(game)
    await mgr.start_all()
    coord = LeadCoordinator(mgr, clock=fake_clock.now_ms)

    # 非游戏主控态不推送
    assert coord.publish_rundown_nudge(segment_id="play_1", segment_title="主线探索", elapsed_ms=900_000) is False
    assert game.nudges == []

    # 切入游戏主控后首次推送成功
    assert await coord.request_game_lead(LeadHandoverContext(segment_id="play_1"), target="solo_game") == "solo_game"
    assert (
        coord.publish_rundown_nudge(
            segment_id="play_1",
            segment_title="主线探索",
            elapsed_ms=960_000,
            expected_ms=900_000,
            next_segment_id="closing",
            next_segment_title="收尾告别",
        )
        is True
    )
    assert len(game.nudges) == 1
    assert game.nudges[0]["next_segment_id"] == "closing"

    # 默认 5 分钟（300_000ms）节流期内同环节不重复推送
    fake_clock.tick(299_999)
    assert coord.publish_rundown_nudge(segment_id="play_1", segment_title="主线探索", elapsed_ms=1_260_000) is False
    assert len(game.nudges) == 1

    # 满 5 分钟后再次推送成功
    fake_clock.tick(1)
    assert coord.publish_rundown_nudge(segment_id="play_1", segment_title="主线探索", elapsed_ms=1_260_000) is True
    assert len(game.nudges) == 2

    # 切到新环节 id 或收到 publish_rundown_update 后立即可推，不受旧环节冷却阻碍
    assert coord.publish_rundown_nudge(segment_id="play_2", segment_title="建仓库", elapsed_ms=660_000) is True
    assert len(game.nudges) == 3

    coord.publish_rundown_update(segment_id="play_2", segment_title="建仓库", segment_goal="续期", expected_ms=600_000)
    assert coord.publish_rundown_nudge(segment_id="play_2", segment_title="建仓库", elapsed_ms=660_000) is True
    assert len(game.nudges) == 4

    await mgr.stop_all()


async def test_fetch_commitment_ledger_normalizes_and_isolates_errors() -> None:
    """fetch_commitment_ledger 按紧凑结构归一化、去重 player_uuids、全空或异常一律返回空字典。"""
    mgr = AgentManager()
    coord = LeadCoordinator(mgr)

    # 未注册回调时返回空字典
    assert await coord.fetch_commitment_ledger(game="beta_game", save_id="s1", player_uuids=("u1",)) == {}

    seen_calls: List[Dict[str, Any]] = []

    async def _ledger_cb(
        *,
        game: str,
        save_id: str = "",
        player_uuids: Any = (),
        limit: int = 8,
    ) -> Dict[str, Any]:
        seen_calls.append({"game": game, "save_id": save_id, "player_uuids": tuple(player_uuids), "limit": limit})
        return {
            "open": [
                {"id": "约#43", "target": "PlayerA", "what": "补还 3 颗钻石", "progress": "已还 2 颗，还欠 1 颗"},
                {"id": "约#44", "target": "", "what": "修好河边木桥"},
                {"id": "约#45", "target": "公共", "what": "扩建仓库"},
            ],
            "closed_this_session": [
                {"id": "约#40", "what": "还回铁镐", "summary": "已放进 PlayerA 箱子"},
            ],
        }

    coord.set_commitment_ledger_provider(_ledger_cb)
    ledger = await coord.fetch_commitment_ledger(
        game="beta_game",
        save_id="save-1",
        player_uuids=("uuid-a", "uuid-a", " ", "uuid-b"),
        limit=2,
    )
    assert seen_calls[-1] == {
        "game": "beta_game",
        "save_id": "save-1",
        "player_uuids": ("uuid-a", "uuid-b"),
        "limit": 2,
    }
    assert ledger == {
        "open": [
            {"id": "约#43", "target": "PlayerA", "what": "补还 3 颗钻石", "progress": "已还 2 颗，还欠 1 颗"},
            {"id": "约#44", "target": "公共", "what": "修好河边木桥"},
        ],
        "closed_this_session": [
            {"id": "约#40", "what": "还回铁镐", "summary": "已放进 PlayerA 箱子"},
        ],
    }

    # 两者皆空时返回 {}（避免尾部状态输出空结构）
    coord.set_commitment_ledger_provider(lambda **_kwargs: {"open": [], "closed_this_session": []})
    assert await coord.fetch_commitment_ledger(game="beta_game", save_id="save-1") == {}

    # 回调抛异常安全降级返回 {}
    async def _boom_ledger(**_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("ledger boom")

    coord.set_commitment_ledger_provider(_boom_ledger)
    assert await coord.fetch_commitment_ledger(game="beta_game", save_id="save-1") == {}

    # 解绑后返回 {}
    coord.set_commitment_ledger_provider(None)
    assert await coord.fetch_commitment_ledger(game="beta_game", save_id="save-1") == {}


async def test_fetch_passive_cues_forwards_exclude_texts_and_keeps_legacy_compat() -> None:
    """fetch_passive_cues 清洗并透传 exclude_texts，同时向后兼容未声明该参数的旧回调签名。"""
    mgr = AgentManager()
    coord = LeadCoordinator(mgr)

    # 1. 新签名回调：准确透传清洗后的 exclude_texts
    new_calls: List[Dict[str, Any]] = []

    async def _new_cb(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        arrived_places: Any = (),
        exclude_texts: Any = (),
        limit: int = 2,
    ) -> List[str]:
        new_calls.append(
            {
                "game": game,
                "texts": texts,
                "players": players,
                "situation": situation,
                "arrived_places": arrived_places,
                "exclude_texts": exclude_texts,
                "limit": limit,
            }
        )
        return ["[存档记忆·记着的事] 营地本来就有一座现成的下界门"]

    coord.set_passive_cue_provider(_new_cb)
    result = await coord.fetch_passive_cues(
        game="sandbox_game",
        texts=("准备砌一座下界门",),
        exclude_texts=("  共享惯例：公共箱子随便拿  ", "", "   ", "环境形态：多人共建生存服"),
        limit=2,
    )
    assert result == ["[存档记忆·记着的事] 营地本来就有一座现成的下界门"]
    assert new_calls[-1]["exclude_texts"] == ("共享惯例：公共箱子随便拿", "环境形态：多人共建生存服")

    # 2. 旧签名回调（未声明 exclude_texts 也未声明 arrived_places / **kwargs）：向后兼容零报错
    legacy_calls: List[Dict[str, Any]] = []

    def _legacy_cb(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        limit: int = 2,
    ) -> List[str]:
        legacy_calls.append(
            {
                "game": game,
                "texts": texts,
                "players": players,
                "situation": situation,
                "limit": limit,
            }
        )
        return ["旧回调线索"]

    coord.set_passive_cue_provider(_legacy_cb)
    legacy_res = await coord.fetch_passive_cues(
        game="sandbox_game",
        texts=("提到木桥",),
        arrived_places=("北边木桥",),
        exclude_texts=("共享惯例：公共箱子随便拿",),
        limit=1,
    )
    assert legacy_res == ["旧回调线索"]
    assert legacy_calls[-1] == {
        "game": "sandbox_game",
        "texts": ("提到木桥",),
        "players": (),
        "situation": None,
        "limit": 1,
    }

    # 3. 接收 **kwargs 的回调同样收到 exclude_texts
    kwargs_seen: List[Dict[str, Any]] = []

    async def _kwargs_cb(**kwargs: Any) -> List[str]:
        kwargs_seen.append(kwargs)
        return ["动态参数线索"]

    coord.set_passive_cue_provider(_kwargs_cb)
    assert await coord.fetch_passive_cues(
        game="sandbox_game",
        texts=("刷石机",),
        exclude_texts=("记着的事：旧设施",),
    ) == ["动态参数线索"]
    assert kwargs_seen[-1]["exclude_texts"] == ("记着的事：旧设施",)
