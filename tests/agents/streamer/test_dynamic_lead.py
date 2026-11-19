"""动态主从主播侧完整能力与四档两级语音打断全链路测试。

覆盖：
1. 自动随开播 / 流程单 talk↔play / 下播切换主控与收尾摘要承接；
2. 游戏不可用被动降级回主播、写入近况并唤醒圆场，以及恢复就绪后周期巡检交回；
3. 交权简报（LeadHandoverContext）组装：流程单、存档名、记忆简报/社交事实、运营备忘、最近 3 句台词；
4. 游戏主控态停用高频 rundown_ready 并启用低频冷场兜底，且冷场兜底轮零游戏控制工具；
5. 观众弹幕轻从路由（隐藏委派/公屏发送工具、保留建议与只读查询、跳过自动沉重速览）与游戏公屏不唤醒主播；
6. 实况解说直通轻量 Replyer（零 Planner 调用）、最小间隔防抖合并、streamer.speech 落库、复位冷场计时与并发串行；
7. spoken echo 零唤醒回传游戏侧；
8. 四档语音优先级与非自选意外阵亡二级硬打断透传及 P2 护栏；
9. lead_mode="streamer_only" 完整回退与提示词改动行动同步守则。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerMouthConfig, StreamerProactiveConfig
from src.agents.streamer.decision_executor import DecisionRoundExecutor
from src.agents.streamer.mouth import Mouth, Trigger
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv, RoundOutcome
from src.agents.streamer.proactive_trigger import ProactiveTrigger
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.scene_card import GoalLine
from src.agents.streamer.session_scope import WindowMaterials
from src.agents.streamer.speech_dispatcher import (
    SPEECH_PRIORITY_AUDIENCE,
    SPEECH_PRIORITY_EMERGENCY,
    SPEECH_PRIORITY_HIGHLIGHT,
    SPEECH_PRIORITY_NORMAL,
    SpeechDispatcher,
)
from src.agents.streamer.stats import StreamerStats
from src.agents.streamer.timeline import GameEventBatch
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.lead import (
    CommentaryRequest,
    LeadCoordinator,
    LeadHandoverContext,
)
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.models.rundown import GAME_OPENING_TALK_RUNDOWN, Rundown
from src.modules.storage.models.social_facts import SocialFact
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.types.game_save import GameSave
from tests.agents.streamer.helpers import ActiveSessionStub, FakeRoundExecutor, make_streamer_agent
from tests.modules.llm.fakes import FakeLLMManager


class _FakeClock:
    """确定性假时钟（毫秒）。"""

    def __init__(self, start_ms: int = 1_800_000_000_000) -> None:
        self.now: int = start_ms

    def __call__(self) -> int:
        return self.now

    def advance(self, delta_ms: int) -> int:
        self.now += delta_ms
        return self.now


class _FakeAutonomousGameAgent(BaseAgent):
    """通用自主领航型假游戏 Agent（刻意不用具体游戏名，验证游戏无关边界）。"""

    name = "adventure"
    description = "通用冒险游戏代理"
    supports_autonomous_lead: ClassVar[bool] = True

    def __init__(self, *, ready: bool = True, yield_summary: str = "完成了营地围墙，待补照明") -> None:
        super().__init__()
        self.ready = ready
        self.yield_summary = yield_summary
        self.enter_contexts: List[LeadHandoverContext] = []
        self.yield_reasons: List[str] = []
        self.spoken_echoes: List[Tuple[str, str, int]] = []
        self.suggestions: List[Tuple[str, str, Optional[List[str]]]] = []
        self.woken_by_echo: bool = False

    async def _on_start(self) -> None:
        return None

    async def _on_stop(self) -> None:
        return None

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def can_take_lead(self) -> bool:
        return self.ready

    async def enter_lead(self, context: LeadHandoverContext) -> bool:
        if not self.ready:
            return False
        self.enter_contexts.append(context)
        return True

    async def yield_lead(self, *, reason: str = "") -> str:
        self.yield_reasons.append(reason)
        return self.yield_summary

    def receive_spoken_echo(self, text: str, *, source: str = "", timestamp_ms: int = 0) -> None:
        self.spoken_echoes.append((text, source, timestamp_ms))

    async def receive_suggestion(
        self,
        content: str,
        *,
        source: str = "streamer",
        source_dialogue: Optional[List[str]] = None,
    ) -> bool:
        self.suggestions.append((content, source, source_dialogue))
        return True

    def receive_delegation(self, *, instruction: str, source: str = "", task_id: str = "") -> bool:
        del instruction, source, task_id
        return True

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        del content, source
        return True


class _FakeRundownRepo:
    """只读流程单仓储替身。"""

    def __init__(self, rundown: Rundown) -> None:
        self._rundown = rundown

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        return self._rundown if rundown_id == self._rundown.rundown_id else None


class _FakeGameToolProvider(BaseToolProvider):
    """带存档读数与只读查询/公屏发送工具的通用游戏工具提供者。"""

    def __init__(self, game_name: str = "adventure", save_name: str = "星空营地") -> None:
        self._game = game_name
        self._save = GameSave(id="save_star", name=save_name)
        self.glance_calls: int = 0

    @property
    def name(self) -> str:
        return self._game

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            ToolSpec(
                name="glance",
                description="读取游戏现场速览（只读）",
                parameters_schema={"type": "object", "properties": {}},
                kind="sync",
                provider=self._game,
                visible_to=["streamer"],
            ),
            ToolSpec(
                name="get_work_log",
                description="读取游戏工作日志（只读）",
                parameters_schema={"type": "object", "properties": {}},
                kind="sync",
                provider=self._game,
                visible_to=["streamer"],
            ),
            ToolSpec(
                name="chat_send",
                description="向游戏公屏发字",
                parameters_schema={"type": "object", "properties": {"message": {"type": "string"}}},
                kind="sync",
                provider=self._game,
                visible_to=["streamer"],
            ),
        ]

    def current_save(self) -> GameSave:
        return self._save

    def guide_for(self, agent_name: str) -> str:
        if agent_name == "streamer":
            return f"{self._game}：支持只读查询。"
        return ""

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        if invocation.tool_name == f"{self._game}_glance":
            self.glance_calls += 1
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=True,
                structured_content={"brief": "在营地门口"},
            )
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})


class _FakeSocialFactRepo:
    """只读游戏社交事实仓储替身。"""

    def __init__(self, facts: List[SocialFact]) -> None:
        self._facts = list(facts)

    async def list_active_facts(self, *, game: Optional[str] = None, save_id: Optional[str] = None) -> List[SocialFact]:
        result: List[SocialFact] = []
        for fact in self._facts:
            if game and fact.game != game:
                continue
            if save_id and fact.save_id and fact.save_id != save_id:
                continue
            result.append(fact)
        return result


@pytest.fixture
def prompts() -> Any:
    reset_prompt_manager()
    yield get_prompt_manager()
    reset_prompt_manager()


# ---------------------------------------------------------------------------
# 1. 自动随开播 / 流程单 talk↔play / 下播切权与收尾摘要承接
# ---------------------------------------------------------------------------


async def test_auto_lead_switch_follows_live_and_rundown_modes(prompts: Any) -> None:
    """开播在 talk 环节保持主播主控；切 play 自动交权；切回 talk 与下播自动收权并记录收尾摘要。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True, yield_summary="刚修好风车并放好工具箱")
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor(result={"speech": "大家晚安呀"})
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "lead_mode": "dynamic"},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        # 1) 未开播：始终保持主播主控
        assert lead_coord.mode == "streamer"
        assert not agent.is_game_lead_active

        # 2) 开播（opening_talk 为 talk 模式）：不交权给游戏
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=101, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.mode == "streamer"
        assert game.enter_contexts == []

        # 3) 流程单切入 main_play（play 模式）：自动交权给就绪游戏 Agent
        ok, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok is True
        assert lead_coord.mode == "game"
        assert lead_coord.active_lead_agent == "adventure"
        assert agent.is_game_lead_active is True
        assert len(game.enter_contexts) == 1
        assert game.enter_contexts[0].segment_id == "main_play"

        # 4) 流程单切入 closing（talk 模式）：自动收回主播主控并把收尾摘要写入近况
        ok, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok is True
        assert lead_coord.mode == "streamer"
        assert not agent.is_game_lead_active
        assert game.yield_reasons == ["rundown_changed"]

        # 验证收尾摘要已写入待入账游戏近况，主播下一窗衔接可见
        window_batch = agent._scope.drain_window()
        assert any("刚修好风车并放好工具箱" in line for line in window_batch.events)

        # 5) 再次切回 main_play 后下播：道别轮执行前先收回游戏主控，摘要落入近况
        ok, _, _ = await agent.rundown_control("goto", segment_id="main_play", now_ms=clock())
        assert ok is True
        assert lead_coord.is_game_lead is True

        game.yield_summary = "收工前把矿车停回了车站"
        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=101, kind="live", ended_at_ms=clock(), duration_ms=600_000),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.mode == "streamer"
        assert "live_ended" in game.yield_reasons
        assert any(call["trigger_cause"].kind is TriggerKind.SIGNOFF for call in round_exec.calls)
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 2. 游戏不可用被动降级回主播并唤醒圆场 + 恢复后自动交回
# ---------------------------------------------------------------------------


async def test_game_unavailable_falls_back_to_streamer_wakes_round_and_recovers(prompts: Any) -> None:
    """游戏断连触发 notify_agent_unavailable 后立即降级回主播、记近况并唤醒圆场；恢复就绪后再交回。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True, yield_summary="矿洞断连前刚采完铁矿")
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    game_round_done = asyncio.Event()

    class _SignalRoundExecutor(FakeRoundExecutor):
        async def execute(self, batch: List[Any], **kwargs: Any) -> Dict[str, Any]:
            res = await super().execute(batch, **kwargs)
            cause = kwargs.get("trigger_cause")
            if kwargs.get("proactive") and cause is not None and cause.kind is TriggerKind.GAME:
                game_round_done.set()
            return res

    round_exec = _SignalRoundExecutor(result={"speech": "呀游戏掉线了，先聊两句等重连！"})
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "proactive": {"enabled": True},
        },
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=102, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 游戏断连不可用 -> 被动降级回主播
        game.ready = False
        await lead_coord.notify_agent_unavailable("adventure", reason="game_unavailable")
        await bus.wait_until_drained()

        assert lead_coord.mode == "streamer"
        # 验证降级提示与收尾摘要写入近况，且立即唤醒主播决策圆场（等待后台 flush_loop 落完该轮）
        await asyncio.wait_for(game_round_done.wait(), timeout=2.0)
        assert any(call["proactive"] and call["trigger_cause"].kind is TriggerKind.GAME for call in round_exec.calls)
        # 恢复就绪后经同步入口自动交回游戏主控
        game.ready = True
        clock.advance(10_000)
        await agent.sync_lead_mode(reason="periodic_sync")
        assert lead_coord.is_game_lead is True
        assert lead_coord.active_lead_agent == "adventure"
    finally:
        await agent.stop()
        await game.stop()


async def test_silent_unready_game_lead_reclaimed_by_sync_and_periodic_check(prompts: Any) -> None:
    """主控游戏静默变为不可用（ready 为空且未自报）时，状态同步与周期巡检均应兜底降级回主播并唤醒圆场。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True, yield_summary="静默掉线前停在城门外")
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    game_round_done = asyncio.Event()

    class _SignalRoundExecutor(FakeRoundExecutor):
        async def execute(self, batch: List[Any], **kwargs: Any) -> Dict[str, Any]:
            res = await super().execute(batch, **kwargs)
            cause = kwargs.get("trigger_cause")
            if kwargs.get("proactive") and cause is not None and cause.kind is TriggerKind.GAME:
                game_round_done.set()
            return res

    round_exec = _SignalRoundExecutor(result={"speech": "好像掉线了，先聊两句等回来！"})
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "proactive": {"enabled": True},
        },
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=103, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 路径一：主控游戏静默不再就绪，显式状态同步立即兜底上报不可用并降级圆场
        game.ready = False
        await agent.sync_lead_mode(reason="manual_sync")
        await bus.wait_until_drained()
        assert lead_coord.mode == "streamer"
        assert lead_coord.snapshot().reason == "game_unavailable"
        await asyncio.wait_for(game_round_done.wait(), timeout=2.0)

        # 恢复就绪并重新进入游戏主控
        game.ready = True
        game_round_done.clear()
        await agent.sync_lead_mode(reason="manual_sync")
        assert lead_coord.is_game_lead is True

        # 路径二：再次静默不再就绪且不手动调用同步，由后台周期巡检自动兜底收权并唤醒主播圆场
        game.ready = False
        await asyncio.wait_for(game_round_done.wait(), timeout=2.0)
        assert lead_coord.mode == "streamer"
        assert lead_coord.snapshot().reason == "game_unavailable"
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 3. 交权简报（LeadHandoverContext）组装
# ---------------------------------------------------------------------------


async def test_handover_context_assembles_rundown_save_memory_notes_and_recent_3_speeches(prompts: Any) -> None:
    """交权简报包含流程单环节、目标游戏存档名、该游戏社交事实简报、待处理运营备忘与最近 3 句台词。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    registry = ToolRegistry()
    game_provider = _FakeGameToolProvider(game_name="adventure", save_name="星海二周目")
    registry.register_provider(game_provider)

    social_repo = _FakeSocialFactRepo(
        [
            SocialFact(
                id=1,
                game="adventure",
                save_id="save_star",
                kind="sharing",
                text="和同伴小周约好共用北边仓库",
                quote="北边仓库大家一起用",
                created_at_ms=clock() - 60_000,
                updated_at_ms=clock() - 60_000,
            ),
            SocialFact(
                id=2,
                game="other_game",
                save_id="other_save",
                kind="sharing",
                text="别的游戏的事实不应混入",
                quote="别的游戏",
                created_at_ms=clock() - 60_000,
                updated_at_ms=clock() - 60_000,
            ),
        ]
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    session_mgr = ActiveSessionStub(active_pk=201)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id},
        prompt=prompts,
        event_bus=bus,
        tool_registry=registry,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        social_fact_repo=social_repo,
        session_manager=session_mgr,
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=201, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()

        # 写入运营备注与 4 句口播（交权简报应保留最近 3 句）
        assert agent.receive_prompt(content="今晚重点修好风车别跑太远", source="operator") is True
        for line in ("第一句旧台词", "第二句开场白", "第三句聊风车", "第四句准备进游戏"):
            await agent._speech.dispatch({"speech": line, "emotion": {"name": "happy", "intensity": 0.5}})

        # 切到 main_play 触发自动交权并校验简报内容
        ok, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok is True
        assert len(game.enter_contexts) == 1
        ctx = game.enter_contexts[0]
        assert ctx.segment_id == "main_play"
        assert ctx.segment_title == "主线游玩推进"
        assert "边玩游戏" in ctx.segment_goal
        assert ctx.expected_ms == 3_600_000
        assert ctx.save_name == "星海二周目"
        assert "和同伴小周约好共用北边仓库" in ctx.memory_brief
        assert "别的游戏的事实不应混入" not in ctx.memory_brief
        assert ctx.operator_notes == ["今晚重点修好风车别跑太远"]
        assert ctx.recent_speech == ["第二句开场白", "第三句聊风车", "第四句准备进游戏"]
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 4. 游戏主控态停用高频自主找话 + 低频冷场兜底 + 兜底轮零游戏工具
# ---------------------------------------------------------------------------


def test_proactive_trigger_skips_rundown_ready_and_uses_idle_fallback_in_game_lead() -> None:
    """游戏主控态跳过高频 rundown_ready，仅连续无声满 game_lead_idle_fallback_ms 才触发冷场兜底。"""
    trigger = ProactiveTrigger(
        StreamerProactiveConfig(enabled=True, rundown_ready_interval_ms=15_000, rundown_think_lead_ms=5_000),
        game_lead_idle_fallback_ms=50_000,
    )
    room = RoomState()
    t0 = 1_000_000
    room.record_speech(t0)

    # 20s / 49.999s：主播主控态早已满足 rundown_ready，但游戏主控态严格静默不抢话
    assert trigger.should_trigger(room, t0 + 20_000, rundown_ready=True, in_game_lead=False) is TriggerKind.RUNDOWN
    assert trigger.should_trigger(room, t0 + 20_000, rundown_ready=True, in_game_lead=True) is None
    assert trigger.should_trigger(room, t0 + 49_999, rundown_ready=True, in_game_lead=True) is None

    # 观众弹幕攒批期间优先让路给弹幕
    assert (
        trigger.should_trigger(
            room,
            t0 + 50_000,
            rundown_ready=True,
            in_game_lead=True,
            viewer_waiting_since_ms=t0 + 49_000,
        )
        is None
    )

    # 连续无声满 50s 且无攒批弹幕 -> 触发低频冷场兜底
    assert (
        trigger.should_trigger(room, t0 + 50_000, rundown_ready=True, in_game_lead=True)
        is TriggerKind.GAME_LEAD_IDLE_FALLBACK
    )
    assert trigger.should_pace_speech(TriggerKind.GAME_LEAD_IDLE_FALLBACK) is False

    # 若任意出口（解说/快嘴/观众回复）出声更新 last_speech_ms -> 立刻复位冷场兜底计时
    room.record_speech(t0 + 45_000)
    assert trigger.should_trigger(room, t0 + 80_000, rundown_ready=True, in_game_lead=True) is None
    assert (
        trigger.should_trigger(room, t0 + 95_000, rundown_ready=True, in_game_lead=True)
        is TriggerKind.GAME_LEAD_IDLE_FALLBACK
    )

    # 沉默轮后进入冷场冷却，防每 tick 空跑
    trigger.record_silent_round(t0 + 95_000)
    assert trigger.should_trigger(room, t0 + 120_000, rundown_ready=True, in_game_lead=True) is None
    assert (
        trigger.should_trigger(room, t0 + 145_000, rundown_ready=True, in_game_lead=True)
        is TriggerKind.GAME_LEAD_IDLE_FALLBACK
    )


async def test_game_lead_idle_fallback_and_viewer_rounds_filter_tools_and_skip_auto_glance(prompts: Any) -> None:
    """轻从模式工具隔离：观众轮隐藏委派与公屏发送、保留建议与只读查询且不自动预抓速览；冷场兜底轮零游戏控制权。"""
    clock = _FakeClock()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, clock=clock)
    await lead_coord.request_game_lead(LeadHandoverContext(segment_id="main_play"), target="adventure", reason="test")
    assert lead_coord.is_game_lead is True

    registry = ToolRegistry()
    game_provider = _FakeGameToolProvider(game_name="adventure")
    registry.register_provider(game_provider)
    registry.register_provider(build_agent_control_provider(manager, lead_coordinator=lead_coord, clock=clock))

    async def _noop(inv: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, structured_content={})

    registry.register_provider(
        make_provider_from_specs(
            "streamer",
            [
                (
                    ToolSpec(
                        name="reply", description="发言", kind="sync", provider="streamer", visible_to=["streamer"]
                    ),
                    _noop,
                )
            ],
        )
    )
    registry.register_provider(
        make_provider_from_specs(
            "rundown",
            [
                (
                    ToolSpec(
                        name="control",
                        description="流程单控制",
                        kind="sync",
                        provider="rundown",
                        visible_to=["streamer"],
                    ),
                    _noop,
                )
            ],
        )
    )
    registry.register_provider(
        make_provider_from_specs(
            "memory",
            [
                (
                    ToolSpec(
                        name="recall",
                        description="回忆",
                        kind="sync",
                        provider="memory",
                        visible_to=["streamer"],
                    ),
                    _noop,
                )
            ],
        )
    )

    captured_calls: List[Dict[str, Any]] = []
    llm = FakeLLMManager()
    orig_generate = llm.generate

    async def _recording_generate(messages: Any, *, tools: Any = None, **kwargs: Any) -> Response:
        captured_calls.append({"messages": list(messages), "tools": list(tools or [])})
        return await orig_generate(messages, tools=tools, **kwargs)

    llm.generate = _recording_generate  # type: ignore[method-assign]

    planner = Planner(
        config=PlannerConfig(reference_tools=["adventure_glance"]),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(game_lead_agent_provider=lambda: lead_coord.active_lead_agent),
    )

    try:
        # 1) 观众弹幕轻从轮：隐藏委派/结单/递话/公屏发字，保留 reply/suggest/memory/只读速览/流程单，且不自动抓速览
        danmaku = RoomMessagePayload(
            message_type="danmaku",
            message_id="m_viewer_1",
            content="能去看看北边遗迹吗",
            user=RoomMessageUser(id="u1", name="小星"),
            timestamp_ms=clock(),
        )
        await planner.plan([danmaku], trigger_cause=TriggerCause(kind=TriggerKind.WINDOW_EXPIRED))
        assert game_provider.glance_calls == 0, "游戏主控态不得每轮前自动预抓沉重速览"
        viewer_tools = {t["name"] for t in captured_calls[0]["tools"]}
        assert {"framework_delegate", "framework_task_settle", "framework_prompt", "adventure_chat_send"}.isdisjoint(
            viewer_tools
        )
        assert {
            "streamer_reply",
            "framework_suggest",
            "memory_recall",
            "adventure_glance",
            "adventure_get_work_log",
            "rundown_control",
        }.issubset(viewer_tools)
        # 系统提示词切为建议说明，不再追加委派强杀守则
        system_text = captured_calls[0]["messages"][0]["content"]
        assert "framework_suggest" in system_text
        assert "when_busy='replace'" not in system_text

        # 2) 游戏主控冷场兜底轮：零游戏控制权（连 framework_suggest 与 task_status 也一并隐藏）
        await planner.plan(
            [],
            proactive=True,
            trigger_cause=TriggerCause(kind=TriggerKind.GAME_LEAD_IDLE_FALLBACK),
        )
        assert game_provider.glance_calls == 0
        fallback_tools = {t["name"] for t in captured_calls[1]["tools"]}
        assert {
            "framework_delegate",
            "framework_task_settle",
            "framework_prompt",
            "framework_suggest",
            "framework_task_status",
            "adventure_chat_send",
        }.isdisjoint(fallback_tools)
        assert {"streamer_reply", "memory_recall", "rundown_control"}.issubset(fallback_tools)
        ref_text = captured_calls[1]["messages"][-1]["content"]
        assert "低频冷场兜底" in ref_text
        assert "【冷场】" in ref_text
    finally:
        await game.stop()


# ---------------------------------------------------------------------------
# 5. 游戏主控态下玩家公屏不唤醒主播决策（只记近况）
# ---------------------------------------------------------------------------


async def test_game_chat_records_context_without_waking_streamer_in_game_lead(prompts: Any) -> None:
    """游戏主控态收到玩家公屏时只记近况不置位主播决策信号；主播主控态照常唤醒。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=301, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True
        round_exec.calls.clear()

        # 游戏主控态下发玩家公屏
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="adventure",
                kind="player",
                sender="同伴甲",
                sender_id="uuid-a",
                sender_online=True,
                content="我把铁矿放进箱子啦",
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert agent._proactive_signals.game_pending is False
        # 近况账本仍已记录，回答观众时知情
        drained = agent._scope.drain_window()
        assert any("我把铁矿放进箱子啦" in line for line in drained.events)
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 6 & 7. 实况解说直通 Replyer + 防抖合并 + 落库 + 复位冷场 + spoken echo 零唤醒回传
# ---------------------------------------------------------------------------


async def test_game_commentary_direct_to_replyer_debounce_speech_emit_and_spoken_echo(prompts: Any) -> None:
    """实况解说严禁走 Planner，直通轻量 Replyer，支持防抖合并、high 绕过、落库、复位冷场并回传 spoken echo。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []

    async def _capture_speech(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _capture_speech, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    llm_calls: List[Dict[str, Any]] = []
    reply_texts = iter(["哎呀挖到一片亮晶晶的蓝矿石啦！", "哇突然塌方了快往后退！"])

    class _CommentaryLLM(FakeLLMManager):
        async def generate(self, input: Any, **kwargs: Any) -> Response:
            llm_calls.append({"messages": list(input) if isinstance(input, list) else input, **kwargs})
            return Response(success=True, content=next(reply_texts), request_id=f"req_{len(llm_calls)}")

    # 用假 Planner 验证实况解说绝对不会调到 Planner
    planner_stub = MagicMock()
    planner_stub.plan = AsyncMock(side_effect=AssertionError("实况解说绝不允许经过重型 Planner"))
    planner_stub.workbench = MagicMock()
    planner_stub.workbench.reset = MagicMock()
    planner_stub.workbench.current_goals = MagicMock(return_value=[])
    planner_stub.reset_player_notes = MagicMock()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "proactive": {"enabled": True},
            "game_lead_commentary_min_interval_ms": 8_000,
            "game_lead_idle_fallback_ms": 50_000,
        },
        llm=_CommentaryLLM(),
        prompt=prompts,
        planner=planner_stub,
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=401, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 1) 首次普通解说：直通 Replyer，发射 streamer.speech，复位 last_speech_ms，回传 spoken echo
        ack1 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="在峡谷底部发现了成片的蓝矿石", mood="excited", urgency="normal"
            ),
        )
        await bus.wait_until_drained()
        assert ack1.accepted is True and ack1.spoken is True and ack1.reason == "dispatched"
        assert planner_stub.plan.await_count == 0
        assert len(llm_calls) == 1 and llm_calls[0]["profile"] == "replyer"
        assert len(speech_events) == 1
        assert speech_events[0].text == "哎呀挖到一片亮晶晶的蓝矿石啦！"
        assert speech_events[0].emotion == "excited"
        assert agent._room_state.last_speech_ms == clock()
        # spoken echo 零唤醒回传给主控游戏
        assert len(game.spoken_echoes) == 1
        assert game.spoken_echoes[0][0] == "哎呀挖到一片亮晶晶的蓝矿石啦！"
        assert game.spoken_echoes[0][1] == "game_commentary"

        # 2) 3 秒后再发普通解说（< 8s）：防抖压下，记入缓冲不调 LLM
        clock.advance(3_000)
        ack2 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="顺手又捡了两根火把", mood="neutral", urgency="normal"),
        )
        assert ack2.accepted is True and ack2.spoken is False and ack2.reason == "throttled_recorded"
        assert len(llm_calls) == 1

        # 3) 1 秒后发 high 紧急解说：绕过防抖立刻播出，并把刚缓冲的摘要合并进本轮 state_facts
        clock.advance(1_000)
        ack3 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="头顶碎石突然塌方砸下来", mood="scared", urgency="high"),
        )
        await bus.wait_until_drained()
        assert ack3.accepted is True and ack3.spoken is True and ack3.reason == "dispatched"
        assert len(llm_calls) == 2
        assert "顺手又捡了两根火把" in llm_calls[1]["messages"][0]["content"]
        assert "头顶碎石突然塌方砸下来" in llm_calls[1]["messages"][0]["content"]
        assert len(speech_events) == 2 and speech_events[1].emotion == "scared"
        assert agent._room_state.last_speech_ms == clock()
        # 解说播出复位了冷场兜底计时器：40s 后仍不触发冷场兜底
        assert (
            agent._proactive_trigger.should_trigger(
                agent._room_state, clock() + 40_000, rundown_ready=True, in_game_lead=True
            )
            is None
        )
        # 满 50s 后才触发
        assert (
            agent._proactive_trigger.should_trigger(
                agent._room_state, clock() + 50_000, rundown_ready=True, in_game_lead=True
            )
            is TriggerKind.GAME_LEAD_IDLE_FALLBACK
        )
    finally:
        await agent.stop()
        await game.stop()


async def test_game_commentary_word_filter_and_serial_lock(prompts: Any) -> None:
    """实况解说命中敏词整条丢弃时返回 spoken=False；并发解说经异步锁串行执行防撞词。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    entered = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    class _ConcurrentLLM(FakeLLMManager):
        async def generate(self, input: Any, **kwargs: Any) -> Response:
            nonlocal in_flight, max_in_flight
            del input, kwargs
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            entered.set()
            await release.wait()
            in_flight -= 1
            return Response(success=True, content="这里包含禁词测试", request_id="req_wf")

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "game_lead_commentary_min_interval_ms": 0,
            "word_filter": {"enabled": True, "words": ["禁词"], "drop_on_match": True},
        },
        llm=_ConcurrentLLM(),
        prompt=prompts,
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=402, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True
        t1 = asyncio.create_task(
            lead_coord.submit_commentary(
                "adventure", CommentaryRequest(game="adventure", summary="第一条", urgency="high")
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        t2 = asyncio.create_task(
            lead_coord.submit_commentary(
                "adventure", CommentaryRequest(game="adventure", summary="第二条", urgency="high")
            )
        )
        await asyncio.sleep(0)
        assert max_in_flight == 1, "并发解说必须串行调用 Replyer"
        release.set()
        ack1, ack2 = await asyncio.gather(t1, t2)
        assert ack1.accepted is True and ack1.spoken is False
        assert ack2.accepted is True and ack2.spoken is False
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 8. 四档优先级与阵亡二级硬打断透传 + 绝不掐断 P2 观众回复
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("batch", "forced", "kind", "expected_priority"),
    [
        ([], True, TriggerKind.FORCED, SPEECH_PRIORITY_AUDIENCE),
        (
            [
                RoomMessagePayload(
                    message_type="danmaku", message_id="m1", content="你好", user=RoomMessageUser(id="u1", name="甲")
                )
            ],
            False,
            TriggerKind.WINDOW_EXPIRED,
            SPEECH_PRIORITY_AUDIENCE,
        ),
        ([], False, TriggerKind.GAME, SPEECH_PRIORITY_HIGHLIGHT),
        ([], False, TriggerKind.COLD, SPEECH_PRIORITY_NORMAL),
        ([], False, TriggerKind.GAME_LEAD_IDLE_FALLBACK, SPEECH_PRIORITY_NORMAL),
    ],
    ids=["forced_paid", "viewer_danmaku", "game_event", "cold_monologue", "game_lead_idle_fallback"],
)
async def test_decision_executor_maps_four_tier_speech_priorities(
    batch: List[RoomMessagePayload],
    forced: bool,
    kind: TriggerKind,
    expected_priority: int,
) -> None:
    """DecisionRoundExecutor 按四档优先级（P2 观众/付费 > P1 游戏 > P0 独白/冷场兜底）透传至 SpeechDispatcher。"""
    outcome = RoundOutcome(
        replied=True,
        target="m1" if batch else None,
        reply_to="m1" if batch else None,
        topic_summary="测试",
        reply_payload={"speech": "收到啦", "emotion": {"name": "happy", "intensity": 0.5}},
    )
    planner = MagicMock()
    planner.plan = AsyncMock(return_value=replace(outcome, raw_content="RAW", llm_request_id="req_p"))
    speech = MagicMock()
    speech.dispatch = AsyncMock(return_value=("收到啦", "happy", "utt_1"))
    proactive_trigger = MagicMock()
    proactive_trigger.should_pace_speech = MagicMock(return_value=False)
    proactive_trigger.speech_release_wait_ms = MagicMock(return_value=0)

    executor = DecisionRoundExecutor(
        planner=planner,
        speech=speech,
        event_bus=None,
        room_state=RoomState(),
        proactive_trigger=proactive_trigger,
        stats=StreamerStats(),
        thinking_sink=None,
        thinking_enabled=False,
        window_opener=AsyncMock(return_value=WindowMaterials(history=[], game_batch=GameEventBatch())),
        rundown_text_provider=MagicMock(return_value=None),
    )

    await executor.execute(batch, forced=forced, trigger_cause=TriggerCause(kind=kind), proactive=not batch)
    assert speech.dispatch.await_args.kwargs["priority"] == expected_priority


async def test_mouth_died_uses_emergency_hard_interrupt_and_protects_p2_audience(prompts: Any) -> None:
    """阵亡反应走 P3 + allow_hard_interrupt=True 掐断在播 P0 独白，且绝不掐断在播 P2 观众回复；高光目击走 P1 不硬打断。"""
    clock = _FakeClock()

    class _MouthLLM:
        def __init__(self, line: str) -> None:
            self._line = line

        async def generate(self, messages: Any, *, system: str = "", profile: str = "") -> Response:
            del messages, system, profile
            return Response(success=True, content=self._line, request_id="req_m")

    # 1) 校验优先级与 allow_hard_interrupt 透传
    recorded: List[Dict[str, Any]] = []

    class _CaptureSpeech:
        async def dispatch_reaction(self, speech: str, **kwargs: Any) -> Optional[str]:
            recorded.append({"speech": speech, **kwargs})
            return "utt_m"

    async def _scene() -> str:
        return "在主世界矿洞"

    mouth = Mouth(
        StreamerMouthConfig(enabled=True),
        llm_service=_MouthLLM("scared：啊我怎么突然没了！"),
        prompt_service=prompts,
        speech=_CaptureSpeech(),
        room_state=RoomState(),
        persona={"bot_name": "麦麦", "personality": "活泼", "style_constraints": "短句", "boundaries": ""},
        speech_max_chars=30,
        word_filter=None,
        goals_provider=lambda: [GoalLine(game="adventure", instruction="探矿", started_ms=clock())],
        segment_provider=lambda: "主线游玩",
        scene_reader=_scene,
        is_live=lambda: True,
        clock=clock,
    )

    await mouth.react_once([Trigger(at_ms=clock(), key="body:died", text="意外摔落阵亡", kind="died")])
    await mouth.react_once([Trigger(at_ms=clock(), key="sighting:rare_ore", text="发现稀有矿脉", kind="rare_sighted")])
    await mouth.react_once(
        [Trigger(at_ms=clock(), key="game:notable_milestone", text="开出稀罕宝物", kind="milestone")]
    )

    assert (recorded[0]["priority"], recorded[0]["allow_hard_interrupt"]) == (SPEECH_PRIORITY_EMERGENCY, True)
    assert (recorded[1]["priority"], recorded[1]["allow_hard_interrupt"]) == (SPEECH_PRIORITY_HIGHLIGHT, False)
    assert (recorded[2]["priority"], recorded[2]["allow_hard_interrupt"]) == (SPEECH_PRIORITY_HIGHLIGHT, False)

    # 2) 与真实 SpeechDispatcher + UtteranceQueue 联动：P3 阵亡硬打断在播 P0 独白，但不掐断在播 P2 观众回复
    bus = EventBus()
    dropped: List[UtteranceDroppedPayload] = []

    async def _on_dropped(_name: str, payload: UtteranceDroppedPayload, _src: str) -> None:
        dropped.append(payload)

    bus.on(CoreEvents.TTS_UTTERANCE_DROPPED, _on_dropped, model_class=UtteranceDroppedPayload)

    playing_started = asyncio.Event()
    played_texts: List[str] = []
    release_p2 = asyncio.Event()

    class _ControllableTTS:
        async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
            del utterance_id
            played_texts.append(text)
            playing_started.set()
            if "常规独白" in text:
                await asyncio.Event().wait()
            elif "回复观众" in text:
                await release_p2.wait()

    dispatcher = SpeechDispatcher(
        event_bus=bus,
        subtitle_service=None,
        tts_engine=_ControllableTTS(),
        speech_config={"enabled": True, "wait_playback": False, "max_queue": 4},
    )
    await dispatcher.start()
    try:
        # 2a) P0 在播时，阵亡反应触发二级硬打断
        p0 = await dispatcher.dispatch({"speech": "这是一句常规独白", "emotion": {}}, priority=SPEECH_PRIORITY_NORMAL)
        assert p0 is not None
        await playing_started.wait()
        playing_started.clear()

        mouth_live = Mouth(
            StreamerMouthConfig(enabled=True, reaction_ttl_ms=500, min_gap_ms=0),
            llm_service=_MouthLLM("scared：救命摔下去了！"),
            prompt_service=prompts,
            speech=dispatcher,
            room_state=RoomState(),
            persona={"bot_name": "麦麦", "personality": "活泼", "style_constraints": "短句", "boundaries": ""},
            speech_max_chars=30,
            word_filter=None,
            goals_provider=lambda: [],
            segment_provider=lambda: "主线游玩",
            scene_reader=_scene,
            is_live=lambda: True,
            clock=clock,
        )
        spoken = await mouth_live.react_once(
            [Trigger(at_ms=clock(), key="body:died", text="意外摔落阵亡", kind="died")]
        )
        assert spoken == "救命摔下去了！"
        await dispatcher.wait_idle(timeout_ms=2_000)
        await bus.wait_until_drained()
        assert any(d.utterance_id == p0[2] and d.reason == "hard_interrupted" for d in dropped)
        assert "救命摔下去了！" in played_texts

        # 2b) P2 观众回复在播时，阵亡反应绝不硬打断正在播放的 P2
        dropped.clear()
        playing_started.clear()
        p2 = await dispatcher.dispatch({"speech": "谢谢回复观众提问", "emotion": {}}, priority=SPEECH_PRIORITY_AUDIENCE)
        assert p2 is not None
        await playing_started.wait()

        clock.advance(20_000)
        death_task = asyncio.create_task(
            mouth_live.react_once([Trigger(at_ms=clock(), key="body:died", text="再次阵亡", kind="died")])
        )
        # 让事件循环推进两拍，使阵亡反应走完 _preempt_queue 判定并进入等槽阶段
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await bus.wait_until_drained()
        assert not any(d.utterance_id == p2[2] and d.reason == "hard_interrupted" for d in dropped)
        release_p2.set()
        await death_task
    finally:
        release_p2.set()
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# 9. lead_mode="streamer_only" 完整回退开关 + 提示词守则回归
# ---------------------------------------------------------------------------


async def test_streamer_only_mode_disables_handover_completely(prompts: Any) -> None:
    """lead_mode='streamer_only' 时同步禁用协调器，进入 play 环节仍保持传统主播主控。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock, enabled=True)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "lead_mode": "streamer_only"},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        assert lead_coord.enabled is False
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=501, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.mode == "streamer"
        assert not agent.is_game_lead_active
        assert game.enter_contexts == []

        # 主播主控态下收到玩家公屏照常置位唤醒信号（传统委派链路完全回退）
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="adventure",
                kind="player",
                sender="同伴乙",
                sender_id="uuid-b",
                sender_online=True,
                content="帮我看下炉子",
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert agent._proactive_signals.game_pending is True
    finally:
        await agent.stop()
        await game.stop()


def test_planner_react_prompt_includes_sync_promised_game_action_rule(prompts: Any) -> None:
    """提示词守则回归：口头或公屏答应改动游戏行动时须同步通过工具通知执行侧。"""
    rendered = prompts.render(
        "amaidesu_planner_react",
        bot_name="麦麦",
        personality="活泼",
        behavior_style="积极互动",
        boundaries_rule="",
        inside_jokes="",
        game_rules="",
        social_fact_step="",
    )
    assert "答应改动游戏行动须同步通知执行侧" in rendered
    assert "同步通过工具将变动落到执行侧" in rendered


async def test_body_event_passes_kind_to_mouth_and_suppresses_planner_wake_in_game_lead(prompts: Any) -> None:
    """游戏主控态下意外阵亡仍驱动快嘴发声（kind='died'），但不再唤醒重型 Planner。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    mouth_stub = MagicMock()
    mouth_stub.start = AsyncMock()
    mouth_stub.stop = AsyncMock()
    mouth_stub.reset = MagicMock()
    mouth_stub.note_game = MagicMock()
    mouth_stub.note_speech = MagicMock()
    mouth_stub.react = MagicMock()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=FakeRoundExecutor(),
        mouth=mouth_stub,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=601, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        await bus.emit(
            CoreEvents.GAME_BODY_DIED,
            BodyEventPayload(game="adventure", kind="died", summary="掉进岩浆意外阵亡", intentional=False),
            source="test",
        )
        await bus.wait_until_drained()

        mouth_stub.react.assert_called_once()
        assert mouth_stub.react.call_args.kwargs["kind"] == "died"
        assert agent._proactive_signals.game_pending is False
    finally:
        await agent.stop()
        await game.stop()
