"""真实 StreamerAgent + 真实 MinecraftAgent 同台动态主从端到端集成测试。

装配真实 EventBus + 真实 ToolRegistry + 真实 AgentManager + 真实 LeadCoordinator +
真实 AgentControlProvider + 真实 StreamerAgent + 真实 MinecraftAgent（仅替换假 LLM
与假 MCP 传输，零假 Agent、零私有成员 patch，假时钟 + wait_until_drained）。

覆盖 5 条核心端到端链路：
1. live.started 并发乱序（StreamerAgent 先跑、MinecraftAgent 后跑）仍零等待自动交权并广播 agent.lead.changed；
2. 真实 MinecraftAgent 调 framework_commentary(about_player=...) -> 真实 StreamerAgent._handle_game_commentary
   -> 真实 Replyer（70 字预算）-> 真实 SpeechDispatcher -> streamer.speech -> spoken echo 零唤醒回传并在下步拼入 [直播间刚说过]；
3. 真实 StreamerAgent 收到含精确坐标的观众弹幕 -> Planner 调 framework_suggest -> 到达真实 MinecraftAgent 的
   [直播间建议] 包含原弹幕坐标来源原话且不取消在跑目标；同时验证游戏主控态下 framework_delegate 与 minecraft_chat_send 隔离/拒收；
4. 游戏主控态下玩家公屏（game.chat.received, kind="player"）直接唤醒真实 MinecraftAgent 且不触发 StreamerAgent Planner；
5. 流程单切回 mode="talk" 与 live.ended 两种收权路径：真实 MinecraftAgent.yield_lead 取消在跑目标并返回客观摘要，
   StreamerAgent 收权落账并恢复主播主控。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.agents import (
    AgentManager,
    LeadCoordinator,
    build_agent_control_provider,
)
from src.modules.events import EventBus, register_core_events
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.agents import AgentLeadChangedPayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.models.rundown import Rundown, RundownSegment
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    LlmScript,
    ScriptLlm,
    _call,
    _resp,
    _texts,
    _wait_until,
    assemble_minecraft_agent,
    offline_config,
)
from tests.agents.streamer.helpers import ActiveSessionStub, make_streamer_agent
from tests.modules.llm.fakes import FakeLLMManager


def _play_then_talk_rundown() -> Rundown:
    """首段为游玩（play）、次段为收尾闲聊（talk）的端到端流程单。"""
    return Rundown(
        rundown_id="e2e_play_then_talk",
        title="双 Agent 动态主从端到端测试单",
        segments=[
            RundownSegment(
                id="seg_play",
                title="主线林地建家",
                task_description="在营地周边砍树采矿并推进小屋建造",
                expected_ms=1_800_000,
                mode="play",
            ),
            RundownSegment(
                id="seg_talk",
                title="收尾闲聊复盘",
                task_description="放下游戏，和观众聊聊今天的收获并收尾",
                expected_ms=600_000,
                mode="talk",
            ),
        ],
    )


class _FakeRundownRepo:
    """只读流程单仓储替身。"""

    def __init__(self, rundown: Rundown) -> None:
        self._rundown = rundown

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        return self._rundown if rundown_id == self._rundown.rundown_id else None


class _ProfileRoutedFakeLLM(FakeLLMManager):
    """基于统一 FakeLLMManager 的按 profile 分流剧本替身（区分 planner 与 replyer 调用）。"""

    def __init__(
        self,
        *,
        planner_handler: Optional[Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Response]] = None,
        replyer_handler: Optional[Callable[[List[Dict[str, Any]], str], Response]] = None,
    ) -> None:
        super().__init__()
        self._planner_handler = planner_handler
        self._replyer_handler = replyer_handler
        self.planner_calls: List[Dict[str, Any]] = []
        self.replyer_calls: List[Dict[str, Any]] = []

    async def generate(self, input: Any, **kwargs: Any) -> Response:
        profile = str(kwargs.get("profile") or "")
        messages = list(input) if isinstance(input, list) else []
        tools = list(kwargs.get("tools") or [])
        system = str(kwargs.get("system") or "")
        record = {"profile": profile, "messages": messages, "tools": tools, "system": system}
        self.calls.append({"method": "generate", "input": input, **kwargs})
        if profile == "planner":
            self.planner_calls.append(record)
            if self._planner_handler is not None:
                return self._planner_handler(messages, tools)
            return Response(success=True, content="", request_id=f"plan_{len(self.planner_calls)}")
        if profile == "replyer":
            self.replyer_calls.append(record)
            if self._replyer_handler is not None:
                return self._replyer_handler(messages, system)
            return Response(success=True, content="好的收到啦！", request_id=f"rep_{len(self.replyer_calls)}")
        return Response(success=True, content="", request_id=f"other_{len(self.calls)}")


@dataclass
class _DualAgentRig:
    """真实双 Agent 端到端联调句柄。"""

    bus: EventBus
    registry: ToolRegistry
    ledger: TaskLedger
    manager: AgentManager
    coordinator: LeadCoordinator
    server: FakeMaicraft
    streamer: StreamerAgent
    minecraft: MinecraftAgent
    streamer_llm: _ProfileRoutedFakeLLM
    minecraft_llm: ScriptLlm
    lead_events: List[AgentLeadChangedPayload]
    speech_events: List[StreamerSpeechPayload]

    async def stop(self) -> None:
        await self.streamer.stop()
        await self.minecraft.stop()
        await self.bus.cleanup()


@pytest.fixture
def prompts() -> Any:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


async def _start_dual_agent_rig(
    *,
    prompts: Any,
    fake_clock: Any,
    mc_script: LlmScript,
    planner_handler: Optional[Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Response]] = None,
    replyer_handler: Optional[Callable[[List[Dict[str, Any]], str], Response]] = None,
    streamer_sections: Optional[Dict[str, Any]] = None,
    rundown: Optional[Rundown] = None,
    start_streamer_first: bool = True,
    on_before_start: Optional[Callable[[LeadCoordinator], None]] = None,
    chat_batch_window_ms: int = 0,
    chat_flush_wait: Optional[Callable[[int], Any]] = None,
    embedding_setup: Optional[Any] = None,
    streamer_extra_kwargs: Optional[Dict[str, Any]] = None,
    work_docs_repo: Optional[Any] = None,
) -> _DualAgentRig:
    """按生产装配拓扑启动真实 StreamerAgent + 真实 MinecraftAgent 同台环境。"""
    register_core_events()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus, clock=fake_clock.now_ms)
    manager = AgentManager(tool_registry=registry)
    coordinator = LeadCoordinator(manager, event_bus=bus, clock=fake_clock.now_ms)
    manager.set_lead_coordinator(coordinator)

    lead_events: List[AgentLeadChangedPayload] = []
    speech_events: List[StreamerSpeechPayload] = []

    async def _on_lead_changed(_name: str, payload: AgentLeadChangedPayload, _src: str) -> None:
        lead_events.append(payload)

    async def _on_speech(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)

    bus.on(CoreEvents.AGENT_LEAD_CHANGED, _on_lead_changed, model_class=AgentLeadChangedPayload)
    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)

    # 注册真实框架工具提供者（含 framework_delegate / framework_suggest / framework_commentary 等）
    control_provider = build_agent_control_provider(
        manager,
        ledger,
        event_bus=bus,
        clock=fake_clock.now_ms,
        lead_coordinator=coordinator,
    )
    registry.register_provider(control_provider)

    # 装配假 MCP 传输（FakeMaicraft）并注册进真实 ToolRegistry
    server = FakeMaicraft()
    server.enter_world("save_e2e_1", "联机冒险二周目")
    registry.register_provider(server)

    # 装配真实 StreamerAgent（真实 Planner + 真实 Replyer + 真实 DecisionRoundExecutor + 真实 SpeechDispatcher）
    effective_rundown = rundown or _play_then_talk_rundown()
    streamer_llm = _ProfileRoutedFakeLLM(
        planner_handler=planner_handler,
        replyer_handler=replyer_handler,
    )
    merged_sections: Dict[str, Any] = {
        "rundown_id": effective_rundown.rundown_id,
        "lead_mode": "dynamic",
        "speech_max_chars": 30,
        "game_lead_commentary_min_interval_ms": 0,
        "proactive": {"enabled": False},
        "background": {"enabled": False},
        "mouth": {"enabled": False},
        "word_filter": {"enabled": False},
        "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
    }
    for key, value in (streamer_sections or {}).items():
        if isinstance(value, dict) and isinstance(merged_sections.get(key), dict):
            merged_sections[key] = {**merged_sections[key], **value}
        else:
            merged_sections[key] = value

    streamer = make_streamer_agent(
        merged_sections,
        llm=streamer_llm,
        prompt=prompts,
        event_bus=bus,
        tool_registry=registry,
        rundown_repo=_FakeRundownRepo(effective_rundown),
        task_ledger=ledger,
        lead_coordinator=coordinator,
        clock=fake_clock.now_ms,
        **(streamer_extra_kwargs or {}),
    )

    # 装配真实 MinecraftAgent（live=None 沿用生产语义：启动落收工态，待 live.started 置位）
    mc_llm = ScriptLlm(mc_script)
    minecraft = assemble_minecraft_agent(
        config=offline_config(events_wait_ms=1000),
        llm=mc_llm,
        bus=bus,
        registry=registry,
        tracker=ledger,
        live=None,
        mcp_client=SimpleNamespace(connected=True),
        mcp_provider=server,
        work_docs_repo=work_docs_repo,
        embedding_setup=embedding_setup,
        lead_coordinator=coordinator,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=chat_batch_window_ms,
        chat_flush_wait=chat_flush_wait,
        clock=lambda: fake_clock.now_ms() / 1000.0,
    )

    # 注册到真实 AgentManager（自动刷新 AgentControlProvider 工具表，纳入 framework_commentary / framework_suggest）
    manager.register(streamer)
    manager.register(minecraft)

    if on_before_start is not None:
        on_before_start(coordinator)

    if start_streamer_first:
        await streamer.start()
        await minecraft.start()
    else:
        await minecraft.start()
        await streamer.start()

    return _DualAgentRig(
        bus=bus,
        registry=registry,
        ledger=ledger,
        manager=manager,
        coordinator=coordinator,
        server=server,
        streamer=streamer,
        minecraft=minecraft,
        streamer_llm=streamer_llm,
        minecraft_llm=mc_llm,
        lead_events=lead_events,
        speech_events=speech_events,
    )


async def _settle_loop(bus: EventBus, turns: int = 8) -> None:
    """排空事件总线并让出若干事件循环拍，供负向断言使用。"""
    await bus.wait_until_drained()
    for _ in range(turns):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 链路 1：live.started 并发乱序零等待自动交权并广播 agent.lead.changed
# ---------------------------------------------------------------------------


async def test_e2e_live_started_out_of_order_hands_over_zero_wait(prompts: Any, fake_clock: Any) -> None:
    """先跑 StreamerAgent._on_live_started、再跑 MinecraftAgent._on_live_started，仍零等待自动交权进入游戏主控。"""
    ready_observations: List[tuple[str, str, str]] = []

    def _bind_spy(coord: LeadCoordinator) -> None:
        # 在 StreamerAgent 启动前挂观察回调：记录 MinecraftAgent 发出就绪通知瞬间的主控态，
        # 验证此时 StreamerAgent 已先跑完 live.started 但因游戏未置位而维持 streamer，随即由就绪回调零等待交权
        coord.bind_ready_listener(lambda name, reason: ready_observations.append((name, reason, coord.mode)))

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=lambda _messages: _resp("已进入自主领航，先观察周围"),
        start_streamer_first=True,
        on_before_start=_bind_spy,
    )
    try:
        assert rig.coordinator.mode == "streamer"
        assert rig.minecraft.can_take_lead() is False

        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=901, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()

        # 显式证明乱序路径发生：MinecraftAgent 置位通知瞬间仍处于 streamer 态，随后经就绪监听器零等待切入 game 态
        assert ready_observations == [("minecraft", "live_started", "streamer")]
        assert rig.coordinator.mode == "game"
        assert rig.coordinator.active_lead_agent == "minecraft"
        assert rig.minecraft.in_lead_mode is True
        assert rig.streamer.is_game_lead_active is True

        assert len(rig.lead_events) == 1
        assert rig.lead_events[0].mode == "game"
        assert rig.lead_events[0].lead_agent == "minecraft"
        assert rig.lead_events[0].previous_lead == "streamer"
        assert rig.lead_events[0].reason == "live_started"

        # 真实 MinecraftAgent 立即进入自主首轮并读到 [主控交接简报]
        await _wait_until(lambda: len(rig.minecraft_llm.seen) >= 1 and not rig.minecraft.batch_active)
        first_prompt = _texts(rig.minecraft_llm.seen[0])
        assert "[主控交接简报]" in first_prompt
        assert "主线林地建家" in first_prompt
    finally:
        await rig.stop()


# ---------------------------------------------------------------------------
# 链路 2：实况解说（about_player 70 字预算）-> streamer.speech -> spoken echo 零唤醒回传
# ---------------------------------------------------------------------------


async def test_e2e_commentary_budget_speech_and_zero_wakeup_spoken_echo(prompts: Any, fake_clock: Any) -> None:
    """MinecraftAgent 调 framework_commentary(about_player) -> Replyer 70 字预算 -> 发声 -> spoken echo 零唤醒写入并在下步可见。"""
    commentary_sent = [False]

    def _mc_script(messages: List[Dict[str, Any]]) -> Response:
        if not commentary_sent[0]:
            commentary_sent[0] = True
            return _resp(
                "把同伴帮忙搬铁锭的事提交给主播解说",
                calls=[
                    _call(
                        "framework_commentary",
                        {
                            "summary": "同伴 ChangingSelf 刚把整箱铁锭搬回营地门口还摆好了熔炉",
                            "mood": "happy",
                            "urgency": "normal",
                            "about_player": "ChangingSelf",
                        },
                        "c_comm_1",
                    )
                ],
            )
        return _resp("收到，继续推进手头进度")

    # 真实 Replyer 调用链：
    # 第 1 次（解说首稿）返回 100+ 字多句长文（含 ChangingSelf），第 2 次（重说步）模拟失败触发 fit_to_budget + 外层 guard(70)；
    # 第 3 次（观众弹幕回复）返回一句简短口播，验证 MinecraftAgent 空闲态收到 spoken echo 严格零唤醒
    replyer_turn = [0]

    def _replyer_handler(_messages: List[Dict[str, Any]], _system: str) -> Response:
        replyer_turn[0] += 1
        if replyer_turn[0] == 1:
            return Response(
                success=True,
                content=(
                    "哇 ChangingSelf 居然帮咱们把整箱铁锭都搬回营地门口啦！"
                    "他还顺手把熔炉和工作台摆得整整齐齐超贴心！"
                    "等会儿咱们去河边抓几只羊带回营地旁边一起搭个大牧场养着玩呀！"
                    "晚上再去北边山坡插满火把防刷怪！"
                ),
                request_id="rep_long_1",
            )
        if replyer_turn[0] == 2:
            return Response(success=False, error="mock_shorten_fallback")
        return Response(success=True, content="谢谢观众提醒，晚点顺路去看看！", request_id="rep_viewer_1")

    planner_replied = [False]

    def _planner_handler(_messages: List[Dict[str, Any]], _tools: List[Dict[str, Any]]) -> Response:
        if not planner_replied[0]:
            planner_replied[0] = True
            return Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc_reply_echo_1",
                        name="streamer_reply",
                        arguments={
                            "topic_summary": "回应观众打招呼并聊一句营地安排",
                            "is_novel": True,
                            "reply_guidance": "自然感谢观众并说晚点顺路去看",
                            "emotion": "happy",
                            "intensity": 0.5,
                        },
                    )
                ],
                request_id="plan_echo_1",
            )
        return Response(success=True, content="", request_id="plan_echo_2")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
        planner_handler=_planner_handler,
        replyer_handler=_replyer_handler,
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=902, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) == 2 and not rig.minecraft.batch_active)
        await rig.bus.wait_until_drained()

        # 1) 解说直通真实 Replyer（零 Planner 调用）并按 about_player 70 字预算截断（> 30 且 <= 70）
        assert len(rig.streamer_llm.planner_calls) == 0
        assert len(rig.speech_events) == 1
        commentary_text = rig.speech_events[0].text
        assert "ChangingSelf" in commentary_text
        assert 30 < len(commentary_text) <= 70
        assert "北边山坡插满火把" not in commentary_text

        # 自产解说回声在紧邻 Step 2 已被剥离，避免模型收到自己刚发出的解说回声诱发二连发风暴
        step2_text = _texts(rig.minecraft_llm.seen[1])
        assert commentary_text not in step2_text
        assert rig.minecraft.observation()["spoken_echo_count"] == 0

        # 2) 游戏侧回到空闲挂起后，主播回应一条观众弹幕发声 -> publish_spoken_echo 写入真实 MinecraftAgent 且严格零额外唤醒
        await rig.bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            RoomMessagePayload(
                message_type="danmaku",
                message_id="m_echo_check",
                content="主播记得晚点看下北边山坡呀",
                user=RoomMessageUser(id="u_echo", name="小月"),
                timestamp_ms=fake_clock.now_ms(),
            ),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        fake_clock.tick(150)
        await _wait_until(lambda: len(rig.speech_events) == 2)
        await _settle_loop(rig.bus)
        viewer_reply_text = rig.speech_events[1].text
        assert "谢谢观众提醒" in viewer_reply_text
        assert len(rig.minecraft_llm.seen) == 2
        assert rig.minecraft.observation()["spoken_echo_count"] == 1

        # 3) 等下一次真实外部事件（身边同伴玩家公屏）唤醒 MinecraftAgent 时，Step 3 推理消息中自动拼入 [直播间刚说过]
        await rig.bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="ChangingSelf",
                sender_id="uuid-cs",
                sender_online=True,
                sender_distance=6.0,
                content="箱子已经整理好啦",
                timestamp_ms=fake_clock.now_ms(),
            ),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) == 3 and not rig.minecraft.batch_active)

        step3_text = _texts(rig.minecraft_llm.seen[2])
        assert "[直播间刚说过]" in step3_text
        assert viewer_reply_text in step3_text
        assert rig.minecraft.observation()["spoken_echo_count"] == 0
    finally:
        await rig.stop()


# ---------------------------------------------------------------------------
# 链路 3：观众含精确坐标弹幕 -> framework_suggest 透传原话且不杀目标 + 工具隔离与业务拒收
# ---------------------------------------------------------------------------


async def test_e2e_viewer_danmaku_suggest_preserves_coords_and_blocks_delegation_and_chat_send(
    prompts: Any,
    fake_clock: Any,
) -> None:
    """观众弹幕经真实 Streamer Planner 调 framework_suggest 透传原话坐标到真实 MinecraftAgent，不打断在跑目标并屏蔽委派/公屏工具。"""
    goal_started = [False]

    def _mc_script(messages: List[Dict[str, Any]]) -> Response:
        if not goal_started[0]:
            goal_started[0] = True
            return _resp(
                "先在营地旁边砍橡木原木",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "砍橡木原木"}},
                        "c_gather_1",
                    )
                ],
            )
        return _resp("收到直播间建议，先把手上砍树跑完再去")

    planner_step = [0]

    def _planner_handler(_messages: List[Dict[str, Any]], _tools: List[Dict[str, Any]]) -> Response:
        planner_step[0] += 1
        if planner_step[0] == 1:
            return Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc_e2e_sug_1",
                        name="framework_suggest",
                        arguments={"content": "观众建议顺路去北边遗迹找下界合金块"},
                    )
                ],
                request_id="plan_sug_1",
            )
        return Response(success=True, content="", request_id="plan_sug_2")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
        planner_handler=_planner_handler,
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=903, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft.tracked_goal_ids) == 1 and not rig.minecraft.batch_active)
        running_goal_id = rig.minecraft.tracked_goal_ids[0]
        assert rig.server.goals[running_goal_id]["state"] == "running"
        mc_turns_before = len(rig.minecraft_llm.seen)

        # 真实观众弹幕进入 EventBus -> 真实 StreamerAgent 攒批刷新 -> 真实 Planner 调 framework_suggest
        await rig.bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            RoomMessagePayload(
                message_type="danmaku",
                message_id="m_e2e_coord",
                content="主播去坐标 (128, 64, -240) 看看，那里藏着下界合金块！",
                user=RoomMessageUser(id="u_viewer_1", name="小星"),
                timestamp_ms=fake_clock.now_ms(),
            ),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        fake_clock.tick(150)
        await _wait_until(lambda: len(rig.minecraft_llm.seen) > mc_turns_before and not rig.minecraft.batch_active)

        # 1) 验证游戏主控态下主播 Planner 工具列表隐藏 framework_delegate / minecraft_chat_send，保留 framework_suggest
        assert len(rig.streamer_llm.planner_calls) >= 1
        planner_tool_names = {tool["name"] for tool in rig.streamer_llm.planner_calls[0]["tools"]}
        assert "framework_suggest" in planner_tool_names
        assert {"framework_delegate", "framework_task_settle", "framework_prompt", "minecraft_chat_send"}.isdisjoint(
            planner_tool_names
        )

        # 2) 验证真实 MinecraftAgent 收到的 [直播间建议] 包含来源原话、精确坐标与物品名，且未取消在跑目标、未建委派账本
        mc_latest = _texts(rig.minecraft_llm.seen[-1])
        assert "[直播间建议]" in mc_latest
        assert "建议内容：观众建议顺路去北边遗迹找下界合金块" in mc_latest
        assert "来源原话：" in mc_latest
        assert "(128, 64, -240)" in mc_latest
        assert "下界合金块" in mc_latest
        assert rig.server.goals[running_goal_id]["state"] == "running"
        assert running_goal_id in rig.minecraft.tracked_goal_ids
        assert all(
            (rec := rig.ledger.get(tid)) is not None and rec.tool != "framework_delegate"
            for tid in rig.ledger.active_task_ids()
        )

        # 3) 即使绕过工具列表直调 framework_delegate 与 minecraft_chat_send，注册表执行层也返回结构化业务拒绝
        blocked_delegate = await rig.registry.invoke(
            ToolInvocation(
                tool_name="framework_delegate",
                arguments={"agent": "minecraft", "instruction": "强行改去挖钻石"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert blocked_delegate.success is False
        assert blocked_delegate.failure_kind == "business"
        assert blocked_delegate.structured_content["error"] == "game_in_lead"

        blocked_chat = await rig.registry.invoke(
            ToolInvocation(
                tool_name="minecraft_chat_send",
                arguments={"text": "主播替你在公屏说话"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert blocked_chat.success is False
        assert blocked_chat.failure_kind == "business"
        assert "主控态" in (blocked_chat.error_message or "")
    finally:
        await rig.stop()


# ---------------------------------------------------------------------------
# 链路 4：游戏内玩家公屏直接唤醒真实 MinecraftAgent 且不触发 StreamerAgent Planner
# ---------------------------------------------------------------------------


async def test_e2e_game_player_chat_wakes_minecraft_without_waking_streamer_planner(
    prompts: Any,
    fake_clock: Any,
) -> None:
    """游戏主控态下玩家公屏（kind='player'）直接唤醒真实 MinecraftAgent 回应，不触发 StreamerAgent Planner。"""
    replied_in_game = [False]

    def _mc_script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if "红石门修好了吗" in joined and not replied_in_game[0]:
            replied_in_game[0] = True
            return _resp(
                "在公屏直接回复同伴",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "chat", "parameters": {"message": "刚修好啦，过来试试！"}}},
                        "c_chat_1",
                    )
                ],
            )
        return _resp("先看着周围")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
        streamer_sections={"proactive": {"enabled": True}},
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=904, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) == 1 and not rig.minecraft.batch_active)
        assert rig.coordinator.mode == "game"

        # 游戏内身边同伴玩家公屏消息到达
        await rig.bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴乙",
                sender_id="uuid-p2",
                sender_online=True,
                sender_distance=8.0,
                content="红石门修好了吗",
                timestamp_ms=fake_clock.now_ms(),
            ),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: replied_in_game[0] and not rig.minecraft.batch_active)
        await _settle_loop(rig.bus)

        # MinecraftAgent 直接经公屏合批读取并公屏回话；StreamerAgent Planner 零触发
        mc_step2 = _texts(rig.minecraft_llm.seen[1])
        assert "[公屏合批]" in mc_step2
        assert "[公屏·身边8格] 同伴乙：红石门修好了吗" in mc_step2
        chat_execs = [
            args for args in rig.server.calls_to("execute") if (args.get("goal") or {}).get("ability") == "chat"
        ]
        assert len(chat_execs) == 1
        assert chat_execs[0]["goal"]["parameters"]["message"] == "刚修好啦，过来试试！"
        assert len(rig.streamer_llm.planner_calls) == 0
    finally:
        await rig.stop()


# ---------------------------------------------------------------------------
# 链路 5：流程单切回 mode="talk" 以及 live.ended 两种收权路径
# ---------------------------------------------------------------------------


async def test_e2e_reclaim_on_rundown_talk_and_live_ended(prompts: Any, fake_clock: Any) -> None:
    """切回 talk 与下播两种收权：真实 MinecraftAgent.yield_lead 取消在跑目标并返回摘要，StreamerAgent 收权落账并恢复主控。"""
    enter_goal_seq = [0]

    def _mc_script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if "[主控交接简报]" in joined and enter_goal_seq[0] == 0:
            enter_goal_seq[0] = 1
            return _resp(
                "先立待办并开始砍橡木盖小屋",
                calls=[
                    _call(
                        "minecraft_todo",
                        {
                            "action": "write",
                            "todos": [
                                {"content": "整理营地工具箱", "status": "done"},
                                {"content": "砍橡木搭屋顶", "status": "in_progress"},
                            ],
                        },
                        "c_todo_1",
                    ),
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "砍橡木搭屋顶"}},
                        "c_gather_1",
                    ),
                ],
            )
        if "[主控交接简报]" in joined and enter_goal_seq[0] == 1 and len(rig.minecraft.tracked_goal_ids) == 0:
            enter_goal_seq[0] = 2
            return _resp(
                "继续第二轮挖铁矿目标",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "采铁矿补工具"}},
                        "c_gather_2",
                    )
                ],
            )
        return _resp("后台目标运行中，等待完成")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
    )
    try:
        # 开播进入 seg_play -> 自动交权给真实 MinecraftAgent
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=905, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft.tracked_goal_ids) == 1 and not rig.minecraft.batch_active)
        goal_1 = rig.minecraft.tracked_goal_ids[0]
        assert rig.server.goals[goal_1]["state"] == "running"

        # 场景 A：流程单切到 seg_talk（mode="talk"）-> 收回主播主控，取消在跑目标并带客观收尾摘要
        ok, _, _ = await rig.streamer.rundown_control("next", now_ms=fake_clock.now_ms())
        assert ok is True
        await rig.bus.wait_until_drained()

        assert rig.coordinator.mode == "streamer"
        assert rig.coordinator.active_lead_agent is None
        assert rig.minecraft.in_lead_mode is False
        assert rig.streamer.is_game_lead_active is False
        assert rig.minecraft.tracked_goal_ids == []
        assert rig.server.goals[goal_1]["state"] == "finished"
        assert rig.server.goals[goal_1]["result"]["status"] == "cancelled"

        reclaim_talk_event = rig.lead_events[-1]
        assert reclaim_talk_event.mode == "streamer"
        assert reclaim_talk_event.previous_lead == "minecraft"
        assert reclaim_talk_event.reason == "rundown_changed"
        assert "整理营地工具箱" in reclaim_talk_event.handover_summary
        assert "砍橡木搭屋顶" in reclaim_talk_event.handover_summary

        # 触发一次主播决策验证：收权摘要已落账进主播上下文，且委派与公屏工具恢复可用
        debug_res = await rig.streamer.debug_test_decision(batch=[{"nickname": "小星", "text": "刚才游戏里忙到哪啦？"}])
        assert debug_res["success"] is True
        assert len(rig.streamer_llm.planner_calls) >= 1
        talk_prompt = _texts(rig.streamer_llm.planner_calls[-1]["messages"])
        assert "整理营地工具箱" in talk_prompt
        restored_tools = {t["name"] for t in rig.streamer_llm.planner_calls[-1]["tools"]}
        assert {"framework_delegate", "minecraft_chat_send"}.issubset(restored_tools)

        # 场景 B：重新切回 seg_play 再次交权并跑起第二个目标，随后触发 live.ended 收权
        ok_back, _, _ = await rig.streamer.rundown_control("goto", segment_id="seg_play", now_ms=fake_clock.now_ms())
        assert ok_back is True
        await rig.bus.wait_until_drained()
        assert rig.coordinator.mode == "game"
        await _wait_until(lambda: len(rig.minecraft.tracked_goal_ids) == 1 and not rig.minecraft.batch_active)
        goal_2 = rig.minecraft.tracked_goal_ids[0]
        assert rig.server.goals[goal_2]["state"] == "running"

        planner_calls_before_end = len(rig.streamer_llm.planner_calls)
        await rig.bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(
                live_session_id=905,
                kind="live",
                ended_at_ms=fake_clock.now_ms(),
                duration_ms=900_000,
            ),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()

        assert rig.coordinator.mode == "streamer"
        assert rig.coordinator.active_lead_agent is None
        assert rig.minecraft.in_lead_mode is False
        assert rig.streamer.is_game_lead_active is False
        assert rig.minecraft.tracked_goal_ids == []
        assert rig.server.goals[goal_2]["state"] == "finished"
        assert rig.server.goals[goal_2]["result"]["status"] == "cancelled"

        reclaim_end_event = rig.lead_events[-1]
        assert reclaim_end_event.mode == "streamer"
        assert reclaim_end_event.previous_lead == "minecraft"
        assert reclaim_end_event.reason == "live_ended"
        assert "采铁矿补工具" in reclaim_end_event.handover_summary

        # 下播道别轮在收权之后执行，且上下文已承接收权摘要
        assert len(rig.streamer_llm.planner_calls) > planner_calls_before_end
        signoff_prompt = _texts(rig.streamer_llm.planner_calls[-1]["messages"])
        assert "采铁矿补工具" in signoff_prompt
    finally:
        await rig.stop()


async def test_e2e_rundown_play_to_play_syncs_new_segment_to_minecraft_handover_brief(
    prompts: Any,
    fake_clock: Any,
) -> None:
    """链路 6：游戏主控期流程单从游玩段 A 切到游玩段 B，经 LeadCoordinator 同步刷新真实 MinecraftAgent 固定层交接简报。"""
    two_play_rundown = Rundown(
        rundown_id="e2e_two_play_segments",
        title="双游玩段端到端流程单",
        segments=[
            RundownSegment(
                id="seg_opening",
                title="开场交代",
                task_description="先盘点营地物资并与观众打招呼",
                expected_ms=600_000,
                mode="play",
            ),
            RundownSegment(
                id="seg_nether",
                title="下界寻堡探险",
                task_description="进入下界寻找下界要塞并收集烈焰棒",
                expected_ms=2_400_000,
                mode="play",
            ),
        ],
    )

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=lambda _m: _resp("明白当前环节"),
        rundown=two_play_rundown,
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=906, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) >= 1 and not rig.minecraft.batch_active)
        assert rig.coordinator.mode == "game"
        assert "开场交代" in _texts(rig.minecraft_llm.seen[0])

        calls_before_next = len(rig.minecraft_llm.seen)
        ok, _, _ = await rig.streamer.rundown_control("next", now_ms=fake_clock.now_ms())
        assert ok is True
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) > calls_before_next and not rig.minecraft.batch_active)

        # 仍保持游戏自主主控态，且游戏侧固定层交接简报已同步更新为新环节，不再残留“开场交代”
        assert rig.coordinator.mode == "game"
        assert rig.minecraft.in_lead_mode is True
        latest_text = _texts(rig.minecraft_llm.seen[-1])
        assert "下界寻堡探险" in latest_text
        assert "进入下界寻找下界要塞并收集烈焰棒" in latest_text
        assert "[流程单切换]" in latest_text
        assert "开场交代" not in latest_text
    finally:
        await rig.stop()


async def test_e2e_silence_nudge_wakes_minecraft_commentary_with_zero_planner_wakeups(
    prompts: Any,
    fake_clock: Any,
) -> None:
    """链路 7：游戏主控期连续静默超过阈值，由前线游戏侧承接静默提醒调用 framework_commentary 暖场且主播 Planner 零唤醒。"""
    goal_started = [False]
    commentary_sent = [False]

    def _mc_script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if not goal_started[0]:
            goal_started[0] = True
            return _resp(
                "先下达采矿后台目标",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "gather", "purpose": "在矿洞深层挖铁矿"}},
                        "c_silence_gather",
                    )
                ],
            )
        if "[直播间静默提醒]" in joined and not commentary_sent[0]:
            commentary_sent[0] = True
            return _resp(
                "顺手向观众汇报正在挖铁矿的进度",
                calls=[
                    _call(
                        "framework_commentary",
                        {
                            "summary": "正在深层矿洞挖铁矿，已经挖到半组准备回营地烧铁",
                            "mood": "happy",
                            "urgency": "normal",
                        },
                        "c_silence_commentary",
                    )
                ],
            )
        return _resp("继续等待后台挖矿目标完成")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
        replyer_handler=lambda _m, _s: Response(
            success=True,
            content="正在深层矿洞挖铁矿呢，已经挖到半组准备回去烧铁啦！",
            request_id="rep_silence_nudge",
        ),
        streamer_sections={
            "game_lead_idle_fallback_ms": 0,
            "game_lead_silence_nudge_ms": 75_000,
            "proactive": {"enabled": True},
        },
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=907, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: len(rig.minecraft.tracked_goal_ids) == 1 and not rig.minecraft.batch_active)
        goal_id = rig.minecraft.tracked_goal_ids[0]
        assert rig.coordinator.mode == "game"
        assert rig.server.goals[goal_id]["state"] == "running"
        assert len(rig.streamer_llm.planner_calls) == 0
        assert len(rig.speech_events) == 0

        # 推进假时钟越过 75s 静默提醒阈值：由 StreamerAgent 触发 publish_silence_nudge 通知游戏侧
        fake_clock.tick(80_000)
        await _wait_until(lambda: len(rig.speech_events) == 1 and not rig.minecraft.batch_active)
        await rig.bus.wait_until_drained()

        assert "挖铁矿" in rig.speech_events[0].text
        # 全程未唤醒主播侧重型 Planner，且游戏后台施工目标未被打断
        assert len(rig.streamer_llm.planner_calls) == 0
        assert rig.minecraft.tracked_goal_ids == [goal_id]
        assert rig.server.goals[goal_id]["state"] == "running"
    finally:
        await rig.stop()


# ---------------------------------------------------------------------------
# 链路 8：游戏主控态 [脑中闪过] 自动浮现跨场与本场同义记忆，附尾保前缀且主播 Planner 0 唤醒
# ---------------------------------------------------------------------------


async def test_e2e_game_lead_passive_flash_surfaces_cross_session_and_short_term_synonym_without_waking_planner(
    prompts: Any,
    fake_clock: Any,
    tmp_path: Any,
) -> None:
    """真实双代理 + SQLite + FakeEmbedding：主控态公屏合批自动附尾 [脑中闪过]（跨场 + 本场同义），主播 Planner 0 唤醒。"""
    from src.modules.llm.embedding import EmbeddingSetup
    from src.modules.storage.database import SQLiteDatabase
    from tests.modules.llm.fakes import FakeEmbeddingServer

    db = SQLiteDatabase(tmp_path / "e2e_flash.db")
    await db.initialize()
    try:
        emb_server = FakeEmbeddingServer()
        emb_client = emb_server.client(clock=fake_clock.now_ms)
        emb_setup = EmbeddingSetup(client=emb_client, note="")

        # 1) 播种跨场档案行（带触发词“主齿轮”，归属当前存档 save_e2e_1）
        old_sid = await db.sessions.insert_live_session(started_at_ms=fake_clock.now_ms() - 86_400_000)
        await db.sessions.close_live_session(live_session_id=old_sid, ended_at_ms=fake_clock.now_ms() - 80_000_000)
        await db.archive.insert_line(
            live_session_id=old_sid,
            text="Minecraft：北岸水车房主齿轮不能拆",
            quote="上次拆了主齿轮整条水车线直接停转",
            evidence_chat_id=1,
            game="minecraft",
            save_id="save_e2e_1",
            save_name="联机冒险二周目",
            keywords=["主齿轮"],
        )
        live_sid = await db.sessions.insert_live_session(started_at_ms=fake_clock.now_ms())

        rule_added = [False]

        def _mc_script(messages: List[Dict[str, Any]]) -> Response:
            if not rule_added[0]:
                rule_added[0] = True
                # 本场钉住一条关于“小猫”的临时规矩（同义测试：后续公屏提“橘猫”，字面不重叠、靠嵌入余弦命中）
                return _resp(
                    "先把本场小猫约定钉住",
                    calls=[
                        _call(
                            "minecraft_rules",
                            {"action": "add", "rule": "仓库门口的猫窝绝对不能挡路", "durable": False},
                            "c_rule_cat",
                        )
                    ],
                )
            return _resp("明白，记在心里了")

        async def _no_real_sleep(_delay_ms: int) -> None:
            return None

        rig = await _start_dual_agent_rig(
            prompts=prompts,
            fake_clock=fake_clock,
            mc_script=_mc_script,
            embedding_setup=emb_setup,
            work_docs_repo=db.game_work_docs,
            chat_batch_window_ms=2_000,
            chat_flush_wait=_no_real_sleep,
            streamer_extra_kwargs={
                "chat_repo": db.chat,
                "sessions_repo": db.sessions,
                "commitments_repo": db.commitments,
                "archive_repo": db.archive,
                "memory_index_repo": db.memory_index,
                "memory_cue_log_repo": db.memory_cue_log,
                "game_player_repo": db.game_players,
                "work_docs_repo": db.game_work_docs,
                "embedding_setup": emb_setup,
                "session_manager": ActiveSessionStub(live_sid),
            },
        )
        try:
            await rig.bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(live_session_id=live_sid, kind="live", started_at_ms=fake_clock.now_ms()),
                source="e2e_test",
            )
            await rig.bus.wait_until_drained()
            await _wait_until(lambda: rule_added[0] and not rig.minecraft.batch_active)
            # 等后台非阻塞短期嵌入任务真正收到并写回向量（避免冷机赌固定事件循环拍数）
            await _wait_until(lambda: any("猫窝" in text for batch in emb_server.requests for text in batch))
            await _settle_loop(rig.bus, turns=12)

            turns_before = len(rig.minecraft_llm.seen)
            fixed_sys_before = dict(rig.minecraft_llm.seen[-1][0])
            fixed_brief_before = dict(rig.minecraft_llm.seen[-1][1])

            # 2) 身边同伴发公屏同时提到“主齿轮”（跨场规则触发）与“橘猫”（本场“猫窝”同义向量命中）；
            # 2 秒合批窗口期间后台预取查询向量完成，窗口到期后 wait_vector_ms=0 即时融合余弦返回
            await rig.bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="同伴丙",
                    sender_id="uuid-p3",
                    sender_online=True,
                    sender_distance=5.0,
                    content="我去看看主齿轮顺便喂下橘猫",
                    timestamp_ms=fake_clock.now_ms(),
                ),
                source="e2e_test",
            )
            await _wait_until(lambda: any("橘猫" in text for batch in emb_server.requests for text in batch))
            await _settle_loop(rig.bus, turns=12)
            fake_clock.tick(2_000)
            assert await rig.minecraft.flush_pending_chat() is True
            await _wait_until(lambda: len(rig.minecraft_llm.seen) > turns_before and not rig.minecraft.batch_active)
            await _settle_loop(rig.bus)

            # 3) 校验尾部 [脑中闪过] 同时浮现跨场线索与本场同义记忆；头部固定层原封不动；主播 Planner 0 唤醒
            latest_messages = rig.minecraft_llm.seen[-1]
            assert latest_messages[0] == fixed_sys_before
            assert latest_messages[1] == fixed_brief_before
            tail_text = str(latest_messages[-1].get("content") or "")
            assert "[公屏合批]" in tail_text
            assert "[脑中闪过]" in tail_text
            assert "北岸水车房主齿轮不能拆" in tail_text
            assert "仓库门口的猫窝绝对不能挡路" in tail_text
            assert len(rig.streamer_llm.planner_calls) == 0
        finally:
            await rig.stop()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 链路 9：memory_recall 查到当前存档 pinned_rules 与游戏侧本场短期记忆，且私聊跨场不外泄
# ---------------------------------------------------------------------------


async def test_e2e_memory_recall_finds_pinned_rules_and_short_term_memory_while_isolating_private_chat(
    prompts: Any,
    fake_clock: Any,
    tmp_path: Any,
) -> None:
    """真实双代理装配：memory_recall 检索到持久 pinned_rules 与游戏侧本场公屏/临时规矩，且私聊在 scope=all 不外泄。"""
    from src.modules.storage.database import SQLiteDatabase

    db = SQLiteDatabase(tmp_path / "e2e_recall.db")
    await db.initialize()
    try:
        live_sid = await db.sessions.insert_live_session(started_at_ms=fake_clock.now_ms())
        # 预置当前存档 save_e2e_1 的持久 pinned_rules
        await db.game_work_docs.save_work_docs(
            game="minecraft",
            save_id="save_e2e_1",
            save_name="联机冒险二周目",
            notebook="北岸水车房已经通电",
            todos=[],
            pinned_rules=["严禁在公共仓库用连锁镐挖地板"],
            updated_session_id=live_sid,
        )

        rig = await _start_dual_agent_rig(
            prompts=prompts,
            fake_clock=fake_clock,
            mc_script=lambda _messages: _resp("明白，先看看周边"),
            work_docs_repo=db.game_work_docs,
            chat_batch_window_ms=0,
            streamer_extra_kwargs={
                "chat_repo": db.chat,
                "sessions_repo": db.sessions,
                "commitments_repo": db.commitments,
                "archive_repo": db.archive,
                "memory_index_repo": db.memory_index,
                "work_docs_repo": db.game_work_docs,
                "session_manager": ActiveSessionStub(live_sid),
            },
        )
        try:
            await rig.bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(live_session_id=live_sid, kind="live", started_at_ms=fake_clock.now_ms()),
                source="e2e_test",
            )
            await rig.bus.wait_until_drained()
            await _wait_until(lambda: len(rig.minecraft_llm.seen) >= 1 and not rig.minecraft.batch_active)

            # 游戏内分别收到一条公开公屏与一条私聊（均收录进 MinecraftAgent 本场短期记忆）
            await rig.bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="同伴丁",
                    sender_id="uuid-p4",
                    sender_online=True,
                    sender_distance=8.0,
                    content="铁矿熔炉在二楼楼梯口右侧",
                    timestamp_ms=fake_clock.now_ms(),
                ),
                source="e2e_test",
            )
            await rig.bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="同伴丁",
                    sender_id="uuid-p4",
                    sender_online=True,
                    private=True,
                    content="悄悄告诉你密室暗门坐标在床底下",
                    timestamp_ms=fake_clock.now_ms(),
                ),
                source="e2e_test",
            )
            await rig.bus.wait_until_drained()
            await _wait_until(lambda: len(rig.minecraft_llm.seen) >= 2 and not rig.minecraft.batch_active)

            # 切回主播主控（talk 模式）刷新存档视角，验证主播侧 memory_recall 既能查到 pinned_rules，也能查到本场短期记忆
            ok, _, _ = await rig.streamer.rundown_control("next", now_ms=fake_clock.now_ms())
            assert ok is True
            await rig.bus.wait_until_drained()
            # 触发一轮主播轻查询确保 _current_saves 已就绪
            await rig.streamer.debug_test_decision(batch=[{"nickname": "小星", "text": "刚才有什么规矩呀？"}])

            # 1) 查长期钉住规矩 pinned_rules
            res_rule = await rig.registry.invoke(
                ToolInvocation(
                    tool_name="memory_recall",
                    arguments={"query": "连锁镐 地板", "scope": "all"},
                    source="planner-react",
                    invoking_agent="streamer",
                )
            )
            assert res_rule.success is True
            assert "当前存档长期规矩" in (res_rule.content or "")
            assert "严禁在公共仓库用连锁镐挖地板" in (res_rule.content or "")

            # 2) 查本场游戏公屏短期记忆（scope=this_stream 与 scope=all 均可见公开公屏）
            res_chat = await rig.registry.invoke(
                ToolInvocation(
                    tool_name="memory_recall",
                    arguments={"query": "铁矿熔炉 二楼", "scope": "this_stream"},
                    source="planner-react",
                    invoking_agent="streamer",
                )
            )
            assert res_chat.success is True
            assert "铁矿熔炉在二楼楼梯口右侧" in (res_chat.content or "")

            # 3) 私聊在 scope="all" 跨场口径检索时绝不外泄
            res_private_all = await rig.registry.invoke(
                ToolInvocation(
                    tool_name="memory_recall",
                    arguments={"query": "密室暗门 床底下", "scope": "all"},
                    source="planner-react",
                    invoking_agent="streamer",
                )
            )
            assert res_private_all.success is True
            assert "密室暗门坐标在床底下" not in (res_private_all.content or "")
        finally:
            await rig.stop()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 链路 10：游戏主控态到达记住地标经真实 MemoryCueProvider 触发跨场地点卡 [脑中闪过]（且本场每地点只推一次）
# ---------------------------------------------------------------------------


async def test_e2e_game_lead_landmark_arrival_triggers_real_memory_cue_provider_once(
    prompts: Any,
    fake_clock: Any,
    tmp_path: Any,
) -> None:
    """真实双代理 + 真实 MemoryCueProvider（非桩）：主控期 Planner 0 唤醒、_arrivals 从未见过远距读数时，
    角色首次走近记住地标仍经显式 arrived_places 触发跨场地点卡 [脑中闪过]，同场离开再返回不重复推。
    """
    from src.modules.storage.database import SQLiteDatabase

    db = SQLiteDatabase(tmp_path / "e2e_landmark.db")
    await db.initialize()
    try:
        old_sid = await db.sessions.insert_live_session(started_at_ms=fake_clock.now_ms() - 86_400_000)
        await db.sessions.close_live_session(live_session_id=old_sid, ended_at_ms=fake_clock.now_ms() - 80_000_000)
        await db.archive.insert_line(
            live_session_id=old_sid,
            text="Minecraft：北岸水车房下面藏着备用齿轮箱",
            quote="上次把备用齿轮箱埋在北岸水车房地板下面了",
            evidence_chat_id=1,
            game="minecraft",
            save_id="save_e2e_1",
            save_name="联机冒险二周目",
            place="北岸水车房",
            keywords=["备用齿轮箱"],
        )
        live_sid = await db.sessions.insert_live_session(started_at_ms=fake_clock.now_ms())

        step = {"n": 0}
        rig_ref: Dict[str, _DualAgentRig] = {}

        def _mc_script(messages: List[Dict[str, Any]]) -> Response:
            step["n"] += 1
            srv = rig_ref["rig"].server
            if step["n"] == 1:
                # 首次从远处 (0,64,0) 移动到北岸水车房跟前 (92,64,0) 并观测自身位置 -> 触发首次到达
                srv.self_view["position"] = {"x": 92.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
                return _resp("走近北岸水车房看看", calls=[_call("maicraft_observe", {"what": "self"}, "obs_arrive_1")])
            if step["n"] == 2:
                # 退回 100 格外再观测一次，让 NearPlaceTracker 记下离开
                srv.self_view["position"] = {"x": 0.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
                return _resp("先回营地拿材料", calls=[_call("maicraft_observe", {"what": "self"}, "obs_leave_1")])
            if step["n"] == 3:
                # 再次走近同一地标并观测自身位置 -> 本场已触发过该地点，不应重复推 [脑中闪过]
                srv.self_view["position"] = {"x": 92.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
                return _resp("再次回到水车房", calls=[_call("maicraft_observe", {"what": "self"}, "obs_arrive_2")])
            return _resp("巡检完毕")

        rig = await _start_dual_agent_rig(
            prompts=prompts,
            fake_clock=fake_clock,
            mc_script=_mc_script,
            work_docs_repo=db.game_work_docs,
            streamer_extra_kwargs={
                "chat_repo": db.chat,
                "sessions_repo": db.sessions,
                "commitments_repo": db.commitments,
                "archive_repo": db.archive,
                "memory_cue_log_repo": db.memory_cue_log,
                "game_player_repo": db.game_players,
                "work_docs_repo": db.game_work_docs,
                "session_manager": ActiveSessionStub(live_sid),
            },
        )
        rig_ref["rig"] = rig
        rig.server.world_memory_view = {
            "places": [
                {
                    "name": "北岸水车房",
                    "position": {"dimension": "minecraft:overworld", "x": 100.0, "y": 64.0, "z": 0.0},
                }
            ],
            "regions": [],
            "records": [],
        }
        rig.server.self_view["position"] = {"x": 0.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
        try:
            # 仅预热 MinecraftAgent 本地 remembered_places 缓存（不调用主播 Planner，也不预喂 MemoryCueProvider._arrivals）
            glance_res = await rig.registry.invoke(
                ToolInvocation(tool_name="minecraft_glance", arguments={}, source="streamer", invoking_agent="streamer")
            )
            assert glance_res.success is True

            await rig.bus.emit(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(live_session_id=live_sid, kind="live", started_at_ms=fake_clock.now_ms()),
                source="e2e_test",
            )
            await rig.bus.wait_until_drained()
            await _wait_until(lambda: len(rig.minecraft_llm.seen) >= 4 and not rig.minecraft.batch_active)
            await _settle_loop(rig.bus)

            # 首次到达后的第 2 步尾部出现真实 MemoryCueProvider 产出的跨场地点卡 [脑中闪过]
            step2_tail = str(rig.minecraft_llm.seen[1][-1].get("content") or "")
            assert "[脑中闪过]" in step2_tail
            assert "北岸水车房下面藏着备用齿轮箱" in step2_tail
            # 头部系统提示词与交接简报全程未被改写
            assert rig.minecraft_llm.seen[0][0] == rig.minecraft_llm.seen[1][0]
            assert rig.minecraft_llm.seen[0][1] == rig.minecraft_llm.seen[1][1]
            # 同场第二次离开再到达同一地标：第 4 步尾部不重复弹出该地点卡
            step4_tail = str(rig.minecraft_llm.seen[3][-1].get("content") or "")
            assert "北岸水车房下面藏着备用齿轮箱" not in step4_tail
            # 全程主播 Planner 零唤醒
            assert len(rig.streamer_llm.planner_calls) == 0
        finally:
            await rig.stop()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 链路 11：双真 Agent 流程单超时提醒闭环（StreamerAgent 超期 -> LeadCoordinator -> MinecraftAgent）
# ---------------------------------------------------------------------------


async def test_e2e_game_lead_rundown_overdue_nudges_real_minecraft_agent(
    prompts: Any,
    fake_clock: Any,
) -> None:
    """真实 StreamerAgent 超期 -> 真实 LeadCoordinator 节流推送 -> 真实 MinecraftAgent 收到 [流程单超时提醒]，主播零唤醒。"""

    def _mc_script(_messages: List[Dict[str, Any]]) -> Response:
        return _resp("收到进度，继续按节奏推进")

    rig = await _start_dual_agent_rig(
        prompts=prompts,
        fake_clock=fake_clock,
        mc_script=_mc_script,
        streamer_sections={
            "game_lead_silence_nudge_ms": 0,
            "proactive": {
                "enabled": True,
                "rundown_overdue_interval_ms": 300_000,
            },
        },
    )
    try:
        await rig.bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=911, kind="live", started_at_ms=fake_clock.now_ms()),
            source="e2e_test",
        )
        await rig.bus.wait_until_drained()
        await _wait_until(lambda: rig.coordinator.is_agent_leading("minecraft") and len(rig.minecraft_llm.seen) >= 1)
        await _wait_until(lambda: not rig.minecraft.batch_active)
        turns_before = len(rig.minecraft_llm.seen)
        assert len(rig.streamer_llm.planner_calls) == 0

        # 拨动时钟刚好到 seg_play 预计时长（1_800_000ms），尚未超期 -> 不推送
        fake_clock.tick(1_800_000)
        await rig.streamer._maybe_flush()
        await _settle_loop(rig.bus)
        assert len(rig.minecraft_llm.seen) == turns_before
        assert len(rig.streamer_llm.planner_calls) == 0

        # 拨动时钟超期（+1_000ms）-> 真实 StreamerAgent 经 LeadCoordinator 推送给真实 MinecraftAgent
        fake_clock.tick(1_000)
        await rig.streamer._maybe_flush()
        await _wait_until(lambda: len(rig.minecraft_llm.seen) > turns_before and not rig.minecraft.batch_active)

        nudge_turn_text = _texts(rig.minecraft_llm.seen[-1])
        assert "[流程单超时提醒]" in nudge_turn_text
        assert "主线林地建家" in nudge_turn_text
        assert "收尾闲聊复盘" in nudge_turn_text
        assert "rundown_control" in nudge_turn_text
        assert len(rig.streamer_llm.planner_calls) == 0

        # 5 分钟节流期内再次 flush 不重复唤醒 MinecraftAgent
        turns_after_first = len(rig.minecraft_llm.seen)
        fake_clock.tick(299_000)
        await rig.streamer._maybe_flush()
        await _settle_loop(rig.bus)
        assert len(rig.minecraft_llm.seen) == turns_after_first
        assert len(rig.streamer_llm.planner_calls) == 0
    finally:
        await rig.stop()
