"""动态主从主播侧完整能力与四档两级语音打断全链路测试。

覆盖：
1. 自动随开播 / 流程单 talk↔play / 下播切换主控与收尾摘要承接；
2. 游戏不可用被动降级回主播、写入近况并唤醒圆场，以及恢复就绪后周期巡检交回；
3. 交权简报（LeadHandoverContext）组装：流程单、存档名、记忆摘要/社交事实、运营备忘、最近 3 句台词；
4. 游戏主控态停用高频 rundown_ready 并启用低频冷场兜底，且冷场兜底轮零游戏控制工具；
5. 观众弹幕轻从路由（隐藏委派/游戏公屏发送工具、保留建议与只读查询、跳过自动沉重速览）与游戏公屏不唤醒主播；
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
from src.agents.streamer.mouth import HIGH_RISK_ATTACKED_KEY, Mouth, Trigger
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv, RoundOutcome
from src.agents.streamer.proactive_trigger import ProactiveTrigger
from src.agents.streamer.replyer import TARGET_EXTRA_CHARS, Replyer
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
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.models.game_players import GamePlayer
from src.modules.storage.models.rundown import GAME_OPENING_TALK_RUNDOWN, Rundown, RundownSegment
from src.modules.storage.models.social_facts import SocialFact
from src.modules.storage.repos.viewers import ViewerPresenceStats
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
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
        self.rundown_updates: List[Tuple[str, str, str, int]] = []
        self.rundown_nudges: List[Dict[str, Any]] = []
        self.silence_nudges: List[int] = []
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

    def receive_rundown_update(
        self,
        *,
        segment_id: str = "",
        segment_title: str = "",
        segment_goal: str = "",
        expected_ms: int = 0,
    ) -> None:
        self.rundown_updates.append((segment_id, segment_title, segment_goal, expected_ms))

    def receive_silence_nudge(self, *, silent_ms: int) -> None:
        self.silence_nudges.append(silent_ms)

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
        self.rundown_nudges.append(
            {
                "segment_id": segment_id,
                "segment_title": segment_title,
                "elapsed_ms": elapsed_ms,
                "expected_ms": expected_ms,
                "next_segment_id": next_segment_id,
                "next_segment_title": next_segment_title,
            }
        )

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
    """带存档读数与只读查询/游戏公屏发送工具的通用游戏工具提供者。"""

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

    # 若任意出口（解说/即时反应/观众回复）出声更新 last_speech_ms -> 立刻复位冷场兜底计时
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
    """轻从模式工具隔离：观众轮隐藏委派与游戏公屏发送、保留建议与只读查询且不自动预抓速览；冷场兜底轮零游戏控制权。"""
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
        # 1) 观众弹幕轻从轮：隐藏委派/结单/递话/游戏公屏发字，保留 reply/suggest/memory/只读速览/流程单，且不自动抓速览
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
# 5. 游戏主控态下玩家游戏公屏不唤醒主播决策（只记近况）
# ---------------------------------------------------------------------------


async def test_game_chat_records_context_without_waking_streamer_in_game_lead(prompts: Any) -> None:
    """游戏主控态收到玩家游戏公屏时只记近况不置位主播决策信号；主播主控态照常唤醒。"""
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

        # 游戏主控态下发玩家游戏公屏
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

        # 2) 3 秒后再发普通解说（< 8s）：防抖压下，仅记游戏近况不调 LLM，也不把旧摘要堆积喂给后续回复器
        clock.advance(3_000)
        ack2 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="顺手又捡了两根火把", mood="neutral", urgency="normal"),
        )
        assert ack2.accepted is True and ack2.spoken is False and ack2.reason == "throttled_recorded"
        assert len(llm_calls) == 1

        # 3) 1 秒后发 high 紧急解说：绕过防抖立刻播出；送入 Replyer 的输入面已净化（不含陈旧节流堆积与过时流程单）
        clock.advance(1_000)
        ack3 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="头顶碎石突然塌方砸下来", mood="scared", urgency="high"),
        )
        await bus.wait_until_drained()
        assert ack3.accepted is True and ack3.spoken is True and ack3.reason == "dispatched"
        assert len(llm_calls) == 2
        prompt_text = llm_calls[1]["messages"][0]["content"]
        assert "顺手又捡了两根火把" not in prompt_text
        assert "头顶碎石突然塌方砸下来" in prompt_text
        assert GAME_OPENING_TALK_RUNDOWN.segments[1].title not in prompt_text
        assert "（当前无流程单）" in prompt_text
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
        async def handle_speech(
            self,
            text: str,
            utterance_id: Optional[str] = None,
            emotion: Optional[str] = None,
            emotion_intensity: Optional[float] = None,
        ) -> None:
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

        # 主播主控态下收到玩家游戏公屏照常置位唤醒信号（传统委派链路完全回退）
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
    """提示词守则回归：口头或游戏公屏答应改动游戏行动时须同步通过工具通知执行侧。"""
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
    """游戏主控态下意外阵亡仍驱动即时反应发声（kind='died'），但不再唤醒重型 Planner。"""
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


# ---------------------------------------------------------------------------
# 10. 实况解说带 about_player 时的外层字数预算兜底（speech_max_chars + 40）
# ---------------------------------------------------------------------------


def test_replyer_budget_for_public_api_matches_mode_and_target(prompts: Any) -> None:
    """Replyer.budget_for 公开只读接口与内部长度预算保持单一事实源（0 表示不限字数）。"""
    replyer = Replyer(
        config={"speech_max_chars": 30, "talk_speech_max_chars": 150},
        llm_service=FakeLLMManager(),
        prompt_service=prompts,
    )
    assert replyer.budget_for("play", has_target=False) == 30
    assert replyer.budget_for("play", has_target=True) == 30 + TARGET_EXTRA_CHARS
    assert replyer.budget_for("talk", has_target=False) == 150
    assert replyer.budget_for("talk", has_target=True) == 150 + TARGET_EXTRA_CHARS

    unlimited = Replyer(
        config={"speech_max_chars": 0, "talk_speech_max_chars": 0},
        llm_service=FakeLLMManager(),
        prompt_service=prompts,
    )
    assert unlimited.budget_for("play", has_target=True) == 0
    assert unlimited.budget_for("talk", has_target=True) == 0


async def test_game_commentary_with_about_player_enforces_target_budget_guard(prompts: Any) -> None:
    """带 about_player 的超长实况解说须由外层守卫截到 speech_max_chars + 40（70 字）以内，不再无上限放行。"""
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

    # 简易假 Replyer（故意不实现 budget_for 也不做内层截短）：专门检验 _handle_game_commentary 的外层极端长文熔断兜底
    canned_speeches = iter(
        [
            # 1) 超过熔断阈值（budget=70, runaway_limit=140）的多句失控长文：应由外层熔断截到 <= 70 字（> 30）
            "刚才小伙伴小羊把整箱铁锭顺手搬到门口啦！他还顺便帮我们把工作台和熔炉都码得整整齐齐的！"
            "后面咱们再去河边看看有没有羊群可以顺路带回营地一起养着玩呀，顺便把牧场栅栏、水车传动轴、漏斗、传送带和仓库储物箱全部重新规划整理一遍！"
            "晚上再去北边山坡把灯插满顺便检查一下周围有没有新矿脉可以挖，顺路再多带几组木头和圆石回来扩建工坊吧！",
            # 2) 55 字两句台词：远低于熔断阈值且无换行，应完整保留不误截
            "刚才小伙伴小羊把铁锭搬到门口了！他还顺便帮我们把熔炉和工作台都码得整整齐齐的呢！",
        ]
    )

    class _SimpleFakeReplyer:
        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            del kwargs
            return {"speech": next(canned_speeches), "llm_request_id": "req_fake_replyer"}

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "speech_max_chars": 30,
            "game_lead_commentary_min_interval_ms": 0,
        },
        prompt=prompts,
        replyer=_SimpleFakeReplyer(),
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
            LiveStartedPayload(live_session_id=701, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 场景 A：超长多句解说带 about_player -> 外层守卫截到 <= 70 字（不再是 0 无上限放行）
        ack1 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure",
                summary="同伴小羊帮忙把铁锭和熔炉整理到营地门口了",
                mood="happy",
                urgency="normal",
                about_player="小羊",
            ),
        )
        await bus.wait_until_drained()
        assert ack1.accepted is True and ack1.spoken is True
        assert len(speech_events) == 1
        assert 30 < len(speech_events[0].text) <= 70
        assert "山坡上把灯插满" not in speech_events[0].text

        # 场景 B：55 字带 about_player 解说 -> 落在 70 字预算内，完整保留
        ack2 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure",
                summary="同伴小羊刚把门口熔炉和工作台摆齐",
                mood="happy",
                urgency="normal",
                about_player="小羊",
            ),
        )
        await bus.wait_until_drained()
        assert ack2.accepted is True and ack2.spoken is True
        assert len(speech_events) == 2
        assert len(speech_events[1].text) > 30
        assert len(speech_events[1].text) <= 70
        assert "码得整整齐齐的呢！" in speech_events[1].text
    finally:
        await agent.stop()
        await game.stop()


async def test_planner_framework_suggest_passes_viewer_source_dialogue_via_attachment(prompts: Any) -> None:
    """主播 Planner 调 framework_suggest 时经 ToolInvocation.attachment 透传本回合观众原话（保留坐标/物品名）。"""
    clock = _FakeClock()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, clock=clock)
    assert (
        await lead_coord.request_game_lead(LeadHandoverContext(segment_id="main_play"), target="adventure")
        == "adventure"
    )

    registry = ToolRegistry()
    registry.register_provider(build_agent_control_provider(manager, lead_coordinator=lead_coord, clock=clock))

    llm = FakeLLMManager(
        responses=[
            Response(
                success=True,
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc_suggest_1",
                        name="framework_suggest",
                        arguments={"content": "观众建议去坐标附近挖下界合金块"},
                    )
                ],
            ),
            Response(success=True, content=""),
        ]
    )
    planner = Planner(
        config=PlannerConfig(),
        llm_service=llm,
        prompt_service=prompts,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(game_lead_agent_provider=lambda: lead_coord.active_lead_agent),
    )

    try:
        danmaku = RoomMessagePayload(
            message_type="danmaku",
            message_id="m_coord_1",
            content="主播去 (128, 64, -240) 看看，那里有下界合金块！",
            user=RoomMessageUser(id="u_star", name="小星"),
            timestamp_ms=clock(),
        )
        await planner.plan([danmaku], trigger_cause=TriggerCause(kind=TriggerKind.WINDOW_EXPIRED))
        assert len(game.suggestions) == 1
        content, source, source_dialogue = game.suggestions[0]
        assert content == "观众建议去坐标附近挖下界合金块"
        assert source == "streamer"
        assert source_dialogue is not None
        assert any("(128, 64, -240)" in line and "下界合金块" in line for line in source_dialogue)
        assert all("[id:m_coord_1]" not in line for line in source_dialogue)
    finally:
        await game.stop()


async def test_lead_ready_listener_respects_all_streamer_gates(prompts: Any) -> None:
    """候选游戏触发 notify_agent_ready 时，主播侧严守未开播/talk 环节/人工覆盖门禁，满足条件即零等待交权。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=False)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "lead_mode": "dynamic"},
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
        # 1) 未开播时收到就绪通知：不交权
        game.ready = True
        await lead_coord.notify_agent_ready("adventure", reason="mcp_reconnected")
        assert lead_coord.mode == "streamer"

        # 2) 开播处于 opening_talk（talk 模式）：收到就绪通知仍不交权
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=702, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await lead_coord.notify_agent_ready("adventure", reason="live_started")
        assert lead_coord.mode == "streamer"

        # 3) 切到 main_play 但先设游戏未就绪，再设人工覆盖锁定 streamer -> 就绪通知被 override 拦下
        game.ready = False
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.mode == "streamer"
        await lead_coord.set_override("streamer", reason="manual_override")
        game.ready = True
        await lead_coord.notify_agent_ready("adventure", reason="mcp_reconnected")
        assert lead_coord.mode == "streamer"

        # 4) 解除覆盖恢复 auto 后再收到 mcp_reconnected 就绪通知 -> 立即零等待交权
        await lead_coord.set_override("auto", reason="manual_override")
        await lead_coord.notify_agent_ready("adventure", reason="mcp_reconnected")
        assert lead_coord.mode == "game"
        assert lead_coord.active_lead_agent == "adventure"
        assert lead_coord.snapshot().reason == "mcp_reconnected"
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 游戏主控态独立轻量进房秒迎通道（合批、零 Planner、精准核销、取消保护）
# ---------------------------------------------------------------------------


async def test_game_lead_enter_welcome_batches_unwelcomed_viewers_via_replyer_without_planner(prompts: Any) -> None:
    """game_lead 下多位未欢迎观众进房经短合并窗直通 Replyer 合为一句欢迎，不受解说刷新计时饿死且精准核销。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []
    speech_emitted = asyncio.Event()

    async def _capture_speech(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)
        speech_emitted.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture_speech, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    # 假 Replyer 记录入参：只点名“小明”和“小红”，故意漏掉“小蓝”以验证不会误标未点名者为已欢迎
    replyer_calls: List[Dict[str, Any]] = []

    class _RecordingReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode
            return 70 if has_target else 30

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            replyer_calls.append(dict(kwargs))
            return {"speech": "欢迎小明和小红来直播间坐坐呀！", "llm_request_id": "req_welcome_1"}

    planner_stub = MagicMock()
    planner_stub.plan = AsyncMock(side_effect=AssertionError("游戏主控轻量进房欢迎绝不能唤醒重型 Planner"))
    planner_stub.workbench = MagicMock()
    planner_stub.workbench.reset = MagicMock()
    planner_stub.workbench.current_goals = MagicMock(return_value=[])
    planner_stub.reset_player_notes = MagicMock()

    wait_started = asyncio.Event()
    release_wait = asyncio.Event()
    wait_delays: List[int] = []

    async def _controlled_wait(delay_ms: int) -> None:
        wait_delays.append(delay_ms)
        wait_started.set()
        await release_wait.wait()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "proactive": {"enabled": True}},
        prompt=prompts,
        planner=planner_stub,
        replyer=_RecordingReplyer(),
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        enter_welcome_debounce_ms=7_000,
        enter_welcome_wait=_controlled_wait,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=801, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 刚发生过实况解说刷新 last_speech_ms，也不得饿死独立进房欢迎通道
        agent._room_state.record_speech(clock())

        # 短合并窗内连续进入三位新观众
        for uid, uname in (("u1", "小明"), ("u2", "小红"), ("u3", "小蓝")):
            await bus.emit(
                CoreEvents.ROOM_MESSAGE_ENTER,
                RoomMessagePayload(
                    message_type="enter",
                    message_id=f"enter_{uid}",
                    content="",
                    user=RoomMessageUser(id=uid, name=uname),
                    timestamp_ms=clock(),
                ),
                source="test",
            )
        await bus.wait_until_drained()
        await asyncio.wait_for(wait_started.wait(), timeout=2.0)
        assert wait_delays == [7_000]

        # 放行短合并窗，三位观众合为一次轻量 Replyer 调用
        release_wait.set()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        assert planner_stub.plan.await_count == 0
        assert len(replyer_calls) == 1
        assert replyer_calls[0]["reply_to"] == ""
        assert list(replyer_calls[0]["keep_names"]) == ["小明", "小红", "小蓝"]
        assert "刚进直播间待欢迎的观众：小明、小红、小蓝" in replyer_calls[0]["state_facts"]
        assert "新观众" not in replyer_calls[0]["state_facts"]
        assert len(speech_events) == 1
        assert speech_events[0].text == "欢迎小明和小红来直播间坐坐呀！"

        # 精准核销：点出名字的小明、小红记入已欢迎表，未点出名字的小蓝不得误标已欢迎
        assert agent._room_state.is_welcomed(user_id="u1", user_name="小明") is True
        assert agent._room_state.is_welcomed(user_id="u2", user_name="小红") is True
        assert agent._room_state.is_welcomed(user_id="u3", user_name="小蓝") is False
        # spoken echo 标记来源为 enter_welcome
        assert game.spoken_echoes[-1][1] == "enter_welcome"

        # 取消保护：新进房排入等待窗后立刻下播，未跑完的欢迎任务必须被取消不再出声
        wait_started.clear()
        release_wait.clear()
        speech_emitted.clear()
        await bus.emit(
            CoreEvents.ROOM_MESSAGE_ENTER,
            RoomMessagePayload(
                message_type="enter",
                message_id="enter_u4",
                content="",
                user=RoomMessageUser(id="u4", name="小紫"),
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await asyncio.wait_for(wait_started.wait(), timeout=2.0)

        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=801, kind="live", ended_at_ms=clock(), duration_ms=60_000),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(replyer_calls) == 1
    finally:
        release_wait.set()
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 开播主控双竞态 + 流程单切段同步 + 消除 game_lead 冗余 proactive:rundown
# ---------------------------------------------------------------------------


async def test_periodic_lead_sync_waits_for_live_start_and_preserves_game_lead_entered_ms(prompts: Any) -> None:
    """开播回调等待记忆摘要刷新期间，周期巡检绝不抢跑交权；且若已处合法主控则不清空进入锚点。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    brief_entered = asyncio.Event()
    release_brief = asyncio.Event()

    class _GatedArchiveRepo:
        async def get_last_archived_session_id(self, **_kwargs: Any) -> Optional[int]:
            return None

        async def list_latest_snapshot(self, **_kwargs: Any) -> List[Any]:
            return []

    class _EmptyCommitmentsRepo:
        async def list_commitments(self, **_kwargs: Any) -> List[Any]:
            brief_entered.set()
            await release_brief.wait()
            return []

    play_first_rundown = Rundown(
        rundown_id="play_first",
        title="直接开玩流程单",
        segments=[
            RundownSegment(
                id="play_1",
                title="第一段游玩",
                task_description="直接进世界建营地",
                expected_ms=1_800_000,
                mode="play",
            )
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": play_first_rundown.rundown_id, "lead_mode": "dynamic"},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(play_first_rundown),
        archive_repo=_GatedArchiveRepo(),
        commitments_repo=_EmptyCommitmentsRepo(),
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        live_task = asyncio.create_task(
            agent._on_live_started(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(live_session_id=802, kind="live", started_at_ms=clock()),
                "test",
            )
        )
        await asyncio.wait_for(brief_entered.wait(), timeout=2.0)

        # 开播初始化仍卡在 _refresh_memory_brief 期间：周期巡检必须早退，绝不抢跑交权
        await agent._maybe_periodic_lead_sync()
        assert lead_coord.mode == "streamer"
        assert game.enter_contexts == []

        # 放行简报刷新后，由 _on_live_started 末尾统一完成首次交权并记录进入锚点
        release_brief.set()
        await live_task
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is True
        assert agent._game_lead_entered_ms == clock()

        # 若已在合法游戏主控下再次经历开播续播回调，不得把有效进入锚点抹为 None
        anchor_before = agent._game_lead_entered_ms
        await agent._on_live_started(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=802, kind="live", started_at_ms=clock(), resumed=True),
            "test",
        )
        assert agent._game_lead_entered_ms == anchor_before
    finally:
        release_brief.set()
        await agent.stop()
        await game.stop()


async def test_rundown_change_in_game_lead_publishes_update_and_skips_redundant_proactive_wake(prompts: Any) -> None:
    """game_lead 下切到非 talk 环节或续时，向主控游戏同步最新环节且不再置位 rundown_pending 唤醒 Planner；切 talk 照常收权唤醒。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    multi_play_rundown = Rundown(
        rundown_id="multi_play",
        title="多游玩段流程单",
        segments=[
            RundownSegment(
                id="play_1", title="建营地", task_description="搭好木屋与仓库", expected_ms=1_800_000, mode="play"
            ),
            RundownSegment(
                id="play_2", title="下矿探险", task_description="采铁矿并找钻石", expected_ms=2_400_000, mode="play"
            ),
            RundownSegment(
                id="closing_talk",
                title="收尾闲聊",
                task_description="总结今天进展并道别",
                expected_ms=600_000,
                mode="talk",
            ),
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"rundown_id": multi_play_rundown.rundown_id, "lead_mode": "dynamic", "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(multi_play_rundown),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=803, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is True
        assert agent._proactive_signals.rundown_pending is False

        # 在 game_lead 下从 play_1 切到 play_2：同步新环节给主控游戏侧，且绝不置位 rundown_pending 触发冗余唤醒
        ok, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok is True
        assert lead_coord.is_game_lead is True
        assert agent._proactive_signals.rundown_pending is False
        assert game.rundown_updates[-1] == ("play_2", "下矿探险", "采铁矿并找钻石", 2_400_000)

        # 在 game_lead 下续时（renew）：同样同步环节且不触发冗余 rundown_pending
        ok_renew, _, _ = await agent.rundown_control("renew", payload={"extra_ms": 600_000}, now_ms=clock())
        assert ok_renew is True
        assert agent._proactive_signals.rundown_pending is False
        assert game.rundown_updates[-1][0] == "play_2"

        # 切入 closing_talk（talk 模式）：收回主控并照常置位 rundown_pending 触发杂谈开场白
        ok_talk, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok_talk is True
        assert lead_coord.mode == "streamer"
        # 切回主播态后，要么信号仍在待消费队列，要么后台 flush 已立即消费并触发 RUNDOWN 决策轮
        await bus.wait_until_drained()
        assert agent._proactive_signals.rundown_pending or any(
            call["trigger_cause"].kind is TriggerKind.RUNDOWN for call in round_exec.calls
        )
    finally:
        await agent.stop()
        await game.stop()


async def test_rundown_change_to_game_segment_without_game_lead_does_not_wake_streamer(prompts: Any) -> None:
    """开播竞态回归：运行时主控仍为主播（主控切换未完成）而流程单停在游戏段时，环节变更不置位主播发言信号（杜绝与游戏侧开场白同秒双白）。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    play_first_rundown = Rundown(
        rundown_id="play_first",
        title="游戏先行流程单",
        segments=[
            RundownSegment(
                id="play_1", title="建营地", task_description="搭好木屋与仓库", expected_ms=1_800_000, mode="play"
            ),
            RundownSegment(
                id="closing_talk",
                title="收尾闲聊",
                task_description="总结今天进展并道别",
                expected_ms=600_000,
                mode="talk",
            ),
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"rundown_id": play_first_rundown.rundown_id, "lead_mode": "dynamic", "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(play_first_rundown),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=804, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is True

        # 人为收回主控制造竞态窗口形态：运行时主控为主播、流程单停在游戏段
        # （等价于开播瞬间 rundown_changed 先于主控切换完成到达）
        await lead_coord.reclaim_streamer_lead(reason="manual")
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is False

        # 游戏段续时：不置位 rundown_pending（修复前该场景会置位并唤醒主播产开场白）
        ok, _, _ = await agent.rundown_control("renew", payload={"extra_ms": 600_000}, now_ms=clock())
        assert ok is True
        await bus.wait_until_drained()
        assert agent._proactive_signals.rundown_pending is False

        # 切到杂谈段：照常置位主播发言信号（或后台 flush 已直接消费触发 RUNDOWN 决策轮）
        ok_talk, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok_talk is True
        await bus.wait_until_drained()
        assert agent._proactive_signals.rundown_pending or any(
            call["trigger_cause"].kind is TriggerKind.RUNDOWN for call in round_exec.calls
        )
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# 实况解说自纠（correction=True）穿透 8s 防抖立即播报
# ---------------------------------------------------------------------------


async def test_game_commentary_correction_bypasses_min_interval_debounce(prompts: Any) -> None:
    """刚播报过解说不足 8s 时，带 correction=True 的普通 urgency 纠偏解说仍直接穿透防抖立即播报。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []

    async def _capture(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lines = iter(["刚才看到一块亮晶晶的钻石矿！", "哎呀看花眼啦，凑近一看其实是铁矿石！"])

    class _SeqReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode, has_target
            return 60

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            del kwargs
            return {"speech": next(lines), "llm_request_id": "req_corr"}

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "game_lead_commentary_min_interval_ms": 8_000,
        },
        prompt=prompts,
        replyer=_SeqReplyer(),
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
            LiveStartedPayload(live_session_id=804, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        ack1 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="洞口看到疑似钻石矿", urgency="normal"),
        )
        await bus.wait_until_drained()
        assert ack1.spoken is True

        # 仅过 2 秒（< 8s）：普通解说应被防抖压下，但 correction=True 必须立刻穿透播报
        clock.advance(2_000)
        ack_normal = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="顺手补一根火把", urgency="normal", correction=False),
        )
        assert ack_normal.spoken is False and ack_normal.reason == "throttled_recorded"

        ack_correction = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure",
                summary="刚才看错了，其实是铁矿不是钻石",
                mood="embarrassed",
                urgency="normal",
                correction=True,
            ),
        )
        await bus.wait_until_drained()
        assert ack_correction.accepted is True and ack_correction.spoken is True
        assert len(speech_events) == 2
        assert "铁矿石" in speech_events[1].text
    finally:
        await agent.stop()
        await game.stop()


# ---------------------------------------------------------------------------
# memory_recall 注册时按能力动态注入自主游戏可见名单回调（零游戏名硬编码）
# ---------------------------------------------------------------------------


async def test_memory_recall_registration_passes_lead_capable_agents_when_supported() -> None:
    """StreamerAgent 注册真实 memory_recall Provider 时动态传入 lead_capable_agents，向自主游戏暴露回忆工具（零硬编码游戏名）。"""
    clock = _FakeClock()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, clock=clock)
    registry = ToolRegistry()

    class _DummyRepo:
        pass

    agent = make_streamer_agent(
        {},
        tool_registry=registry,
        lead_coordinator=lead_coord,
        chat_repo=_DummyRepo(),
        commitments_repo=_DummyRepo(),
        archive_repo=_DummyRepo(),
        round_executor=FakeRoundExecutor(),
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        adventure_tools = {spec.full_name for spec in registry.list_tools(for_agent="adventure")}
        streamer_tools = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
        other_tools = {spec.full_name for spec in registry.list_tools(for_agent="other_plain_game")}
        assert "memory_recall" in adventure_tools
        assert "memory_recall" in streamer_tools
        assert "memory_recall" not in other_tools
    finally:
        await agent.stop()
        await game.stop()


async def test_enter_welcome_skips_duplicate_when_concurrent_round_welcomed_during_generation(prompts: Any) -> None:
    """enter_welcome 在 Replyer 生成飞行期间若并发轮次已欢迎同批观众，播报前须剥除重复欢迎避免双迎。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []

    async def _capture(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    gen_started = asyncio.Event()
    release_gen = asyncio.Event()

    class _GatedWelcomeReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode
            return 70 if has_target else 30

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            del kwargs
            gen_started.set()
            await release_gen.wait()
            return {"speech": "欢迎小明进来坐呀！", "llm_request_id": "req_flight"}

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id},
        prompt=prompts,
        replyer=_GatedWelcomeReplyer(),
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        enter_welcome_debounce_ms=0,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=805, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        await bus.emit(
            CoreEvents.ROOM_MESSAGE_ENTER,
            RoomMessagePayload(
                message_type="enter",
                message_id="enter_flight",
                content="",
                user=RoomMessageUser(id="u_xm", name="小明"),
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await asyncio.wait_for(gen_started.wait(), timeout=2.0)

        # 生成飞行期间并发路径已经欢迎过小明并核销已欢迎表
        agent._room_state.note_spoken("欢迎小明来直播间呀！")
        release_gen.set()
        if agent._enter_welcome_task is not None:
            await agent._enter_welcome_task
        await bus.wait_until_drained()

        # 飞行期间已被覆盖，不再重复播报第二句欢迎
        assert speech_events == []
    finally:
        release_gen.set()
        await agent.stop()
        await game.stop()


async def test_outer_guard_preserves_real_replyer_names_without_secondary_truncation(prompts: Any) -> None:
    """真实 Replyer 补名后若总长略超外层预算，外层兜底不得二次切句把第二句里的观众称呼切丢。"""
    clock = _FakeClock()
    # 构造真实 Replyer 实例（budget_for("play", has_target=True) == 30 + 40 == 70）
    agent = make_streamer_agent(
        {"speech_max_chars": 30},
        prompt=prompts,
        clock=clock,
    )
    assert isinstance(agent._replyer, Replyer)

    # 模拟真实 Replyer 补名后总长超过 70 字（两句分别含“小明”与“小红”，若外层按 70 字二次截句会切掉第二句导致丢掉“小红”）
    restored_speech = (
        "欢迎小明来到直播间一起看咱们慢慢玩呀，今天正在河边把营地外墙、储物仓库、工作台和照明火把一并收拾整齐顺便再看看附近有没有新矿脉！"
        "也欢迎小红进来坐坐呀！"
    )
    assert len(restored_speech) > 70

    guarded = agent._guard_replyer_speech(
        restored_speech,
        reply_to="小明、小红",
        required_names=["小明", "小红"],
        log_label="轻量进房欢迎",
    )
    assert guarded == restored_speech
    assert "小明" in guarded and "小红" in guarded


# ---------------------------------------------------------------------------
# 游戏主控静默暖场提醒 + 封堵游戏主控期误唤醒信号 + 运营递话转发主控游戏
# ---------------------------------------------------------------------------


async def test_game_lead_silence_nudge_triggers_after_threshold_respects_cooldown_and_resets_on_speech(
    prompts: Any,
) -> None:
    """game_lead 连续无声满 game_lead_silence_nudge_ms 且过冷却时通知主控游戏，主播 Planner 零唤醒，新发言清冷却。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "proactive": {"enabled": True},
            "game_lead_idle_fallback_ms": 0,
            "game_lead_silence_nudge_ms": 75_000,
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
            LiveStartedPayload(live_session_id=806, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True
        round_exec.calls.clear()

        # 未满 75 秒不发送静默提醒
        clock.advance(74_999)
        await agent._maybe_flush()
        assert game.silence_nudges == []
        assert round_exec.calls == []

        # 满 75 秒触发首次静默提醒，且不唤醒主播 Planner
        clock.advance(1)
        await agent._maybe_flush()
        assert game.silence_nudges == [75_000]
        assert round_exec.calls == []

        # 冷却期内（再过 30 秒）不重复发送提醒
        clock.advance(30_000)
        await agent._maybe_flush()
        assert game.silence_nudges == [75_000]

        # 满第二个冷却周期（距上次提醒满 75 秒）再次发送提醒
        clock.advance(45_000)
        await agent._maybe_flush()
        assert game.silence_nudges == [75_000, 150_000]

        # 任一出口新发言（如游戏解说/即时反应/欢迎）刷新 last_speech_ms 并清零静默提醒冷却
        await agent._speech.dispatch(
            {"speech": "这边刚把仓库屋顶补好啦！", "emotion": {"name": "happy", "intensity": 0.5}}
        )
        agent._room_state.record_speech(clock())
        await bus.wait_until_drained()
        clock.advance(74_999)
        await agent._maybe_flush()
        assert len(game.silence_nudges) == 2

        clock.advance(1)
        await agent._maybe_flush()
        assert game.silence_nudges == [75_000, 150_000, 75_000]
        assert round_exec.calls == []
    finally:
        await agent.stop()
        await game.stop()


async def test_game_lead_blocks_set_game_leaks_and_forwards_operator_prompt_as_suggestion(prompts: Any) -> None:
    """game_lead 下封堵 _on_mouth_superseded / on_task_notification 置位 game_pending，且运营递话异步转发主控游戏。"""
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
            LiveStartedPayload(live_session_id=807, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 嘴覆盖撤回路径：game_lead 下即使账本有新事件也不置位 game_pending
        agent._game_ledger.record_event("adventure", "milestone", "刚到达东岸码头", clock())
        agent._on_mouth_superseded()
        assert agent._proactive_signals.game_pending is False

        # 任务停滞告警路径：game_lead 下记账但不置位 game_pending 唤醒 Planner
        alert_payload = TaskChangedPayload(
            task_id="deleg_test_1",
            source="agent",
            provider="framework",
            tool="framework_delegate",
            initiator="streamer",
            executor="adventure",
            status="running",
            alert=True,
            summary="委派长时间无活动",
            snapshot={"own_words": "去采木头"},
            timestamp_ms=clock(),
        )
        agent.on_task_notification(alert_payload)
        assert agent._proactive_signals.game_pending is False

        # 运营/控制台递话在 game_lead 下额外异步转发非强杀建议给主控游戏侧
        assert agent.receive_prompt(content="先优先把仓库灯插满再探索", source="operator") is True
        assert agent.receive_prompt(content="别跑太远，十分钟后准备收尾", source="dashboard") is True
        if agent._memory_tasks:
            await asyncio.gather(*list(agent._memory_tasks), return_exceptions=True)
        assert ("先优先把仓库灯插满再探索", "operator", None) in game.suggestions
        assert ("别跑太远，十分钟后准备收尾", "operator", None) in game.suggestions
    finally:
        await agent.stop()
        await game.stop()


async def test_game_lead_rundown_overdue_pushes_throttled_nudge_without_waking_streamer(prompts: Any) -> None:
    """游戏主控期游玩段超期：下一段仍是游玩段时按节流推送提醒；下一段为收尾 talk 段或已是末段时不催促自动切段。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    multi_stage_rundown = Rundown(
        rundown_id="multi_stage_play",
        title="多阶段游玩流程单",
        segments=[
            RundownSegment(
                id="opening_talk", title="开场热身", task_description="和观众打招呼", expected_ms=300_000, mode="talk"
            ),
            RundownSegment(
                id="play_1", title="营地建设", task_description="建好基础工坊", expected_ms=1_800_000, mode="play"
            ),
            RundownSegment(
                id="play_2", title="矿洞探险", task_description="下矿采集矿石", expected_ms=1_800_000, mode="play"
            ),
            RundownSegment(
                id="closing", title="收尾闲聊", task_description="总结今晚并道别", expected_ms=300_000, mode="talk"
            ),
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {
            "rundown_id": multi_stage_rundown.rundown_id,
            "proactive": {"enabled": True, "rundown_overdue_interval_ms": 300_000},
        },
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(multi_stage_rundown),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=808, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        # 切入 play_1（expected_ms=1_800_000，下一段为 play_2 游玩段）并进入游戏主控态
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True
        round_exec.calls.clear()
        game.rundown_nudges.clear()

        # 未超时不推送
        clock.advance(1_800_000)
        await agent._maybe_flush()
        assert game.rundown_nudges == []
        assert round_exec.calls == []

        # 超期（> 1_800_000ms）且下一段为游玩段 -> 触发首次推送给主控游戏侧，主播零越权唤醒
        clock.advance(1_000)
        await agent._maybe_flush()
        assert len(game.rundown_nudges) == 1
        nudge = game.rundown_nudges[0]
        assert nudge["segment_id"] == "play_1"
        assert nudge["elapsed_ms"] == 1_801_000
        assert nudge["expected_ms"] == 1_800_000
        assert nudge["next_segment_id"] == "play_2"
        assert round_exec.calls == []

        # 5 分钟节流期内不重复推送
        clock.advance(299_000)
        await agent._maybe_flush()
        assert len(game.rundown_nudges) == 1
        assert round_exec.calls == []

        # 满 5 分钟节流后再次推送，主播仍不越权唤醒
        clock.advance(1_000)
        await agent._maybe_flush()
        assert len(game.rundown_nudges) == 2
        assert round_exec.calls == []

        # 切入 play_2（下一段是 closing 收尾 talk 段）：超期不推送闹钟，把收尾切段权留给运营者或下播流程
        await agent.rundown_control("next", now_ms=clock())
        game.rundown_nudges.clear()
        clock.advance(1_801_000)
        await agent._maybe_flush()
        assert game.rundown_nudges == []
        assert round_exec.calls == []
    finally:
        await agent.stop()
        await game.stop()

    # 单游玩末段（无下一段）：超期同样不推送超时闹钟催促游戏自动结束流程单
    single_play_rundown = Rundown(
        rundown_id="single_play_last",
        title="单段自由游玩单",
        segments=[
            RundownSegment(
                id="only_play", title="自由游玩", task_description="持续探险", expected_ms=1_200_000, mode="play"
            )
        ],
    )
    clock2 = _FakeClock()
    bus2 = EventBus()
    manager2 = AgentManager()
    game2 = _FakeAutonomousGameAgent(ready=True)
    manager2.register(game2)
    await game2.start()
    lead_coord2 = LeadCoordinator(manager2, event_bus=bus2, clock=clock2)
    agent2 = make_streamer_agent(
        {
            "rundown_id": single_play_rundown.rundown_id,
            "proactive": {"enabled": True, "rundown_overdue_interval_ms": 300_000},
        },
        prompt=prompts,
        event_bus=bus2,
        rundown_repo=_FakeRundownRepo(single_play_rundown),
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord2,
        clock=clock2,
    )
    manager2.register(agent2)
    await agent2.start()
    try:
        await bus2.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=818, kind="live", started_at_ms=clock2()),
            source="test",
        )
        await bus2.wait_until_drained()
        assert lead_coord2.is_game_lead is True
        clock2.advance(1_201_000)
        await agent2._maybe_flush()
        assert game2.rundown_nudges == []
    finally:
        await agent2.stop()
        await game2.stop()


async def test_signoff_and_ended_state_blocks_rundown_control_mutations(prompts: Any) -> None:
    """下播后（not is_live / 收播道别态）经 ToolRegistry 调用 rundown_control 被只读闸拦截，零播后 rundown.changed。"""
    clock = _FakeClock()
    bus = EventBus()
    registry = ToolRegistry()
    rundown_events: List[Any] = []

    async def _on_rundown_changed(_e: str, payload: Any, _s: str) -> None:
        rundown_events.append(payload)

    bus.on(CoreEvents.RUNDOWN_CHANGED, _on_rundown_changed, model_class=RundownChangedPayload)

    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        tool_registry=registry,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        round_executor=round_exec,
        clock=clock,
    )
    await agent.start()

    try:
        # 1) 开播前未直播态调用 rundown_control 被只读闸拦下
        pre_live = await registry.invoke(
            ToolInvocation(
                tool_name="rundown_control",
                arguments={"action": "next"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert pre_live.structured_content["ok"] is False
        assert pre_live.structured_content["reason"] == "not_live_readonly"

        # 2) 开播进行中（跨过首段预期时长）可正常推进流程单
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=809, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        clock.advance(300_000)
        live_res = await registry.invoke(
            ToolInvocation(
                tool_name="rundown_control",
                arguments={"action": "next"},
                source="planner-react",
                invoking_agent="streamer",
            )
        )
        assert live_res.structured_content["ok"] is True
        await bus.wait_until_drained()

        # 3) 下播后（live.ended）所有写操作均被拦截，绝不产出播后 rundown.changed 事件
        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=809),
            source="test",
        )
        await bus.wait_until_drained()
        rundown_events.clear()

        for action, extra in [("next", {}), ("renew", {}), ("goto", {"segment_id": "closing"})]:
            post_res = await registry.invoke(
                ToolInvocation(
                    tool_name="rundown_control",
                    arguments={"action": action, **extra},
                    source="planner-react",
                    invoking_agent="streamer",
                )
            )
            assert post_res.structured_content["ok"] is False
            assert post_res.structured_content["reason"] == "not_live_readonly"
        await bus.wait_until_drained()
        assert rundown_events == []
    finally:
        await agent.stop()


async def test_danmaku_quiet_window_flushes_single_fast_and_merges_burst_with_fake_clock(prompts: Any) -> None:
    """假时钟端到端验证：单条观众弹幕 800ms 静默快放，连发弹幕自动顺延合并为一批送入决策轮。"""
    clock = _FakeClock()
    bus = EventBus()
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {
            "batch": {
                "batch_window_ms": 3_000,
                "danmaku_quiet_window_ms": 800,
                "enable_idle_compensation": True,
                "batch_max_wait_ms": 30_000,
            },
            "proactive": {"enabled": False},
        },
        prompt=prompts,
        event_bus=bus,
        round_executor=round_exec,
        clock=clock,
    )
    await agent.start()

    try:
        # 单条弹幕：t+799 未放行，t+800 立即放行（远早于 3000ms）
        await agent.handle_message(
            RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u1", name="观众甲"),
                content="晚上好呀",
            )
        )
        clock.advance(799)
        await agent._maybe_flush()
        assert len(round_exec.calls) == 0

        clock.advance(1)
        await agent._maybe_flush()
        assert len(round_exec.calls) == 1
        assert [m.content for m in round_exec.calls[0]["batch"]] == ["晚上好呀"]

        # 连发 2 条弹幕（间隔 500ms）：不触发 9.5s 空窗补偿拖延，距第 2 条满 800ms 合并放行
        round_exec.calls.clear()
        await agent.handle_message(
            RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u2", name="观众乙"),
                content="这波操作好秀",
            )
        )
        clock.advance(500)
        await agent.handle_message(
            RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u2", name="观众乙"),
                content="能不能再演示一遍",
            )
        )
        clock.advance(799)
        await agent._maybe_flush()
        assert len(round_exec.calls) == 0

        clock.advance(1)
        await agent._maybe_flush()
        assert len(round_exec.calls) == 1
        assert [m.content for m in round_exec.calls[0]["batch"]] == ["这波操作好秀", "能不能再演示一遍"]
    finally:
        await agent.stop()


async def test_enter_welcome_with_real_replyer_never_renders_game_companion_and_preserves_names(
    prompts: Any,
) -> None:
    """轻量进房欢迎接入真实 Replyer 时，绝不渲染「提及的游戏同伴」，且初稿与缩句重试均必保观众昵称。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []
    speech_emitted = asyncio.Event()

    async def _capture(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)
        speech_emitted.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    # 首稿触发极端长文熔断（> max(65*2, 120) = 130）：纯确定性截断，零二次 LLM 呼叫，仍完整保留 ViewerA 与 ViewerB
    long_draft = (
        "欢迎ViewerA和ViewerB来到直播间一起玩呀！"
        "今天咱们在工坊旁边搭水车、齿轮箱和矿车轨道，刚刚还在研究怎么把传送带和漏斗全都接起来，顺便把仓库箱子里的铁矿、煤炭和红石全部分类整理干净呢！"
        "待会儿还要去东边河岸继续探索新矿洞看看有没有新发现，晚上再去北边山坡插满火把呀！"
    )
    assert len(long_draft) > 130
    llm_calls: List[Dict[str, Any]] = []
    responses = iter(
        [
            Response(success=True, content=long_draft, request_id="req_w_orig"),
        ]
    )

    class _InspectLLM(FakeLLMManager):
        async def generate(self, input: Any, **kwargs: Any) -> Response:
            llm_calls.append({"messages": input, **kwargs})
            return next(responses)

    real_replyer = Replyer(
        config={"bot_name": "麦麦", "speech_max_chars": 25},
        llm_service=_InspectLLM(),
        prompt_service=prompts,
    )
    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "speech_max_chars": 25},
        prompt=prompts,
        replyer=real_replyer,
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        enter_welcome_debounce_ms=0,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=806, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        for uid, uname in (("u_a", "ViewerA"), ("u_b", "ViewerB")):
            await bus.emit(
                CoreEvents.ROOM_MESSAGE_ENTER,
                RoomMessagePayload(
                    message_type="enter",
                    message_id=f"enter_{uid}",
                    content="",
                    user=RoomMessageUser(id=uid, name=uname),
                    timestamp_ms=clock(),
                ),
                source="test",
            )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        assert len(llm_calls) == 1
        initial_prompt = llm_calls[0]["messages"][0]["content"]
        assert "提及的游戏同伴" not in initial_prompt
        assert "刚在游戏里找过她" not in initial_prompt
        assert "刚进直播间待欢迎的观众：ViewerA、ViewerB" in initial_prompt
        assert "待欢迎的新观众" not in initial_prompt and "每一位新观众" not in initial_prompt
        assert "ViewerA" in initial_prompt and "ViewerB" in initial_prompt
        assert len(speech_events) == 1
        assert speech_events[0].text == "欢迎ViewerA和ViewerB来到直播间一起玩呀！"
        assert "ViewerA" in speech_events[0].text and "ViewerB" in speech_events[0].text
    finally:
        await agent.stop()
        await game.stop()


async def test_framework_commentary_same_round_dedupes_without_blocking_cross_round_or_high_urgency(
    prompts: Any,
) -> None:
    """同轮普通解说二连发在协调器与主播侧均静默去重，不产 throttled_recorded；跨轮与高紧急度正常播报。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_events: List[StreamerSpeechPayload] = []

    async def _capture(_name: str, payload: StreamerSpeechPayload, _src: str) -> None:
        speech_events.append(payload)

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    replyer_calls: List[Dict[str, Any]] = []
    speeches = iter(
        [
            "先把矿车轨道铺到矿洞门口啦！",
            "哇前面突然刷出一只苦力怕，赶紧撤！",
            "新一轮继续把水车齿轮接上吧！",
        ]
    )

    class _CountingReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode, has_target
            return 60

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            replyer_calls.append(dict(kwargs))
            return {"speech": next(speeches), "llm_request_id": f"req_c_{len(replyer_calls)}"}

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "game_lead_commentary_min_interval_ms": 8_000,
        },
        prompt=prompts,
        replyer=_CountingReplyer(),
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
            LiveStartedPayload(live_session_id=807, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 1) 同轮首次普通解说正常播出
        ack1 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="矿车轨道已经铺到矿洞门口", urgency="normal", round_id="mc_round_1"
            ),
        )
        await bus.wait_until_drained()
        assert ack1.accepted is True and ack1.spoken is True and ack1.reason == "dispatched"
        assert len(replyer_calls) == 1

        # 2) 3 秒后同轮二连发普通解说 → 直接同轮去重，不调 Replyer，不产出 throttled_recorded
        clock.advance(3_000)
        ack2 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="矿车轨道铺完了准备下矿", urgency="normal", round_id="mc_round_1"
            ),
        )
        assert ack2.accepted is True and ack2.spoken is False and ack2.reason == "same_round_deduped"
        assert len(replyer_calls) == 1

        # 3) 即使已超过 8 秒间隔，同轮普通解说仍防抖拦截；但同轮 high 紧急度穿透播报
        clock.advance(10_000)
        ack3 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="还在同一轮补充一句", urgency="normal", round_id="mc_round_1"),
        )
        assert ack3.accepted is True and ack3.spoken is False and ack3.reason == "same_round_deduped"
        assert len(replyer_calls) == 1

        ack_high = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="前方突发苦力怕贴脸", urgency="high", mood="scared", round_id="mc_round_1"
            ),
        )
        await bus.wait_until_drained()
        assert ack_high.accepted is True and ack_high.spoken is True and ack_high.reason == "dispatched"
        assert len(replyer_calls) == 2

        # 4) 跨轮新 round_id 且过间隔 → 正常播出；随后短间隔内无 round_id 重复摘要亦去重不积压缓冲
        clock.advance(9_000)
        ack_next_round = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="新一轮把水车齿轮接好了", urgency="normal", round_id="mc_round_2"
            ),
        )
        await bus.wait_until_drained()
        assert ack_next_round.accepted is True and ack_next_round.spoken is True
        assert len(replyer_calls) == 3

        clock.advance(3_000)
        ack_dup_text = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="新一轮把水车齿轮接好了", urgency="normal"),
        )
        assert (
            ack_dup_text.accepted is True
            and ack_dup_text.spoken is False
            and ack_dup_text.reason == "same_round_deduped"
        )
        assert len(speech_events) == 3
    finally:
        await agent.stop()
        await game.stop()


async def test_lead_switch_clears_streamer_commentary_debounce_anchors(prompts: Any) -> None:
    """切入新游戏主控与收回主播主控时，主播侧同步清空同轮与短间隔防抖锚点，不串旧要点也不误拦同轮号。"""
    clock = _FakeClock()
    bus = EventBus()

    class _SecondGameAgent(_FakeAutonomousGameAgent):
        name = "builder"
        description = "第二款自主领航游戏代理"

    manager = AgentManager()
    game_a = _FakeAutonomousGameAgent(ready=True)
    game_b = _SecondGameAgent(ready=True)
    manager.register(game_a)
    manager.register(game_b)
    await game_a.start()
    await game_b.start()

    replyer_calls: List[Dict[str, Any]] = []

    class _RecordingReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode, has_target
            return 60

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            replyer_calls.append(dict(kwargs))
            plan = kwargs["plan"]
            return {"speech": f"播报：{plan.topic_summary}", "llm_request_id": f"req_sw_{len(replyer_calls)}"}

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {
            "rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id,
            "game_lead_commentary_min_interval_ms": 8_000,
        },
        prompt=prompts,
        replyer=_RecordingReplyer(),
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
            LiveStartedPayload(live_session_id=810, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()

        # 进入甲游戏主控，建立主播侧同轮锚点与防抖缓冲
        assert (
            await lead_coord.request_game_lead(LeadHandoverContext(segment_id="main_play"), target="adventure")
            == "adventure"
        )
        await bus.wait_until_drained()

        ack_a1 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="甲号首轮探矿进展", urgency="normal", round_id="round_1"),
        )
        await bus.wait_until_drained()
        assert ack_a1.spoken is True and ack_a1.reason == "dispatched"

        clock.advance(2_000)
        ack_a2 = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="甲号滞留缓冲旧要点", urgency="normal", round_id="round_2"),
        )
        assert ack_a2.accepted is True and ack_a2.spoken is False and ack_a2.reason == "throttled_recorded"

        # 切入乙游戏主控（payload.mode == "game"）：主播侧解说缓冲与全部防抖锚点同步清空
        assert (
            await lead_coord.request_game_lead(
                LeadHandoverContext(segment_id="main_play"), target="builder", reason="switch_game"
            )
            == "builder"
        )
        await bus.wait_until_drained()

        # 1) 切入乙游戏后发 high 解说：输入面干净，只带乙号本轮摘要，不带甲号被节流旧要点与过时流程单
        clock.advance(1_000)
        ack_b_high = await lead_coord.submit_commentary(
            "builder",
            CommentaryRequest(game="builder", summary="乙号突发高光时刻", urgency="high"),
        )
        await bus.wait_until_drained()
        assert ack_b_high.spoken is True
        assert replyer_calls[-1]["plan"].topic_summary == "乙号突发高光时刻"
        assert replyer_calls[-1]["state_facts"] == ""
        assert replyer_calls[-1]["rundown"] is None
        assert "甲号滞留缓冲旧要点" not in replyer_calls[-1]["plan"].topic_summary

        # 2) 同轮锚点已清：过最小间隔后乙游戏使用与甲游戏相同的 round_id="round_1" 不被误拦
        clock.advance(9_000)
        ack_b_same_round = await lead_coord.submit_commentary(
            "builder",
            CommentaryRequest(game="builder", summary="乙号同名轮次新解说", urgency="normal", round_id="round_1"),
        )
        await bus.wait_until_drained()
        assert ack_b_same_round.spoken is True and ack_b_same_round.reason == "dispatched"

        # 3) 短间隔重复摘要锚点已清：切回甲游戏后短间隔内提交相同摘要，走常规间隔防抖而非 same_round_deduped
        assert (
            await lead_coord.request_game_lead(
                LeadHandoverContext(segment_id="main_play"), target="adventure", reason="switch_back"
            )
            == "adventure"
        )
        await bus.wait_until_drained()
        clock.advance(1_000)
        ack_a_after_switch = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="乙号同名轮次新解说", urgency="normal"),
        )
        assert ack_a_after_switch.accepted is True
        assert ack_a_after_switch.spoken is False
        assert ack_a_after_switch.reason == "throttled_recorded"

        # 4) 收回主播主控（payload.previous_lead != self.name）：清空全部防抖锚点
        clock.advance(9_000)
        ack_before_reclaim = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(
                game="adventure", summary="收权前正常播报", urgency="normal", round_id="round_before_reclaim"
            ),
        )
        await bus.wait_until_drained()
        assert ack_before_reclaim.spoken is True

        clock.advance(2_000)
        ack_buffered_before_reclaim = await lead_coord.submit_commentary(
            "adventure",
            CommentaryRequest(game="adventure", summary="收权前滞留要点", urgency="normal", round_id="round_buf"),
        )
        assert ack_buffered_before_reclaim.reason == "throttled_recorded"

        await lead_coord.reclaim_streamer_lead(reason="rundown_changed")
        await bus.wait_until_drained()
        assert agent._last_commentary_round_id == ""
        assert agent._last_commentary_summary == ""
        assert agent._last_commentary_spoken_ms is None
    finally:
        await agent.stop()
        await game_a.stop()
        await game_b.stop()


async def test_live_start_in_progress_blocks_premature_sync_lead_and_proactive_round(prompts: Any) -> None:
    """开播初始化卡在记忆摘要刷新期间，流程单 begin 不得抢跑派发后台切权，且主动发言轮也不得抢跑触发。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    registry = ToolRegistry()
    registry.register_provider(_FakeGameToolProvider(game_name="adventure", save_name="二周目星海"))

    brief_entered = asyncio.Event()
    release_brief = asyncio.Event()

    class _GatedArchiveRepo:
        async def get_last_archived_session_id(self, **_kwargs: Any) -> Optional[int]:
            return None

        async def list_latest_snapshot(self, **_kwargs: Any) -> List[Any]:
            return []

    class _GatedCommitmentsRepo:
        async def list_commitments(self, **_kwargs: Any) -> List[Any]:
            brief_entered.set()
            await release_brief.wait()
            return []

    play_first_rundown = Rundown(
        rundown_id="play_first_race",
        title="游玩首段竞态单",
        segments=[
            RundownSegment(
                id="play_1",
                title="直接开玩",
                task_description="进存档继续修水车",
                expected_ms=1_800_000,
                mode="play",
                notes="先检查仓库库存",
            )
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {"rundown_id": play_first_rundown.rundown_id, "lead_mode": "dynamic", "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        tool_registry=registry,
        rundown_repo=_FakeRundownRepo(play_first_rundown),
        archive_repo=_GatedArchiveRepo(),
        commitments_repo=_GatedCommitmentsRepo(),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        live_task = asyncio.create_task(
            agent._on_live_started(
                CoreEvents.LIVE_STARTED,
                LiveStartedPayload(live_session_id=901, kind="live", started_at_ms=clock()),
                "test",
            )
        )
        await asyncio.wait_for(brief_entered.wait(), timeout=2.0)

        # 等待事件总线排空并让出事件循环给任何可能误派的后台任务：初始化未完期间绝不可提前交权，也不得抢跑主动发言
        await bus.wait_until_drained()
        assert lead_coord.mode == "streamer"
        assert game.enter_contexts == []
        assert await agent._evaluate_proactive_round() is False
        assert round_exec.calls == []

        # 放行记忆摘要刷新：由 _on_live_started 末尾一次性交权，且拿到存档名与环节备注
        release_brief.set()
        await live_task
        await bus.wait_until_drained()

        assert lead_coord.is_game_lead is True
        assert len(game.enter_contexts) == 1
        assert game.enter_contexts[0].save_name == "二周目星海"
        assert game.enter_contexts[0].segment_notes == "先检查仓库库存"
        assert game.enter_contexts[0].rundown_finished is False
        assert round_exec.calls == []
    finally:
        release_brief.set()
        await agent.stop()
        await game.stop()


async def test_rundown_ready_blocked_in_dynamic_play_segment_but_allowed_in_streamer_only(prompts: Any) -> None:
    """动态游戏主控的游玩段在未切权窗口内拦截 rundown_ready 防双开场白；streamer_only 游玩段照常允许。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    # 故意设为未就绪，稳定复现“开播后处于游玩段但尚未完成向游戏交权（in_game_lead=False）”的窗口
    game = _FakeAutonomousGameAgent(ready=False)
    manager.register(game)
    await game.start()

    play_first_rundown = Rundown(
        rundown_id="play_ready_gate",
        title="游玩首段门禁测试单",
        segments=[
            RundownSegment(id="play_1", title="主线游玩", task_description="建工坊", expected_ms=1_800_000, mode="play")
        ],
    )

    # 1) dynamic 模式：in_game_lead=False 且处于 play 段，rundown_ready 不抢跑唤醒主播 Planner
    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    dyn_exec = FakeRoundExecutor()
    dyn_agent = make_streamer_agent(
        {"rundown_id": play_first_rundown.rundown_id, "lead_mode": "dynamic", "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(play_first_rundown),
        round_executor=dyn_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(dyn_agent)
    await dyn_agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=902, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is False
        assert await dyn_agent._evaluate_proactive_round() is False
        assert dyn_exec.calls == []
    finally:
        await dyn_agent.stop()

    # 2) streamer_only 模式：同样处于 play 段，主播自主承接，rundown_ready 照常唤醒
    solo_exec = FakeRoundExecutor()
    solo_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    solo_agent = make_streamer_agent(
        {"rundown_id": play_first_rundown.rundown_id, "lead_mode": "streamer_only", "proactive": {"enabled": True}},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(play_first_rundown),
        round_executor=solo_exec,
        lead_coordinator=solo_coord,
        clock=clock,
    )
    await solo_agent.start()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=903, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert await solo_agent._evaluate_proactive_round() is True
        assert any(call["trigger_cause"].kind is TriggerKind.RUNDOWN for call in solo_exec.calls)
    finally:
        await solo_agent.stop()
        await game.stop()


async def test_rundown_finish_and_segment_notes_sync_to_game_lead_and_handover(prompts: Any) -> None:
    """交权与切段透传 segment_notes，且流程单推进至 finish（无当前环节）时向游戏主控显式下发 rundown_finished=True。"""

    class _ExtendedAutonomousGame(_FakeAutonomousGameAgent):
        def __init__(self) -> None:
            super().__init__(ready=True)
            self.ext_updates: List[Dict[str, Any]] = []

        def receive_rundown_update(
            self,
            *,
            segment_id: str = "",
            segment_title: str = "",
            segment_goal: str = "",
            expected_ms: int = 0,
            segment_notes: str = "",
            rundown_finished: bool = False,
        ) -> None:
            super().receive_rundown_update(
                segment_id=segment_id,
                segment_title=segment_title,
                segment_goal=segment_goal,
                expected_ms=expected_ms,
            )
            self.ext_updates.append(
                {
                    "segment_id": segment_id,
                    "segment_notes": segment_notes,
                    "rundown_finished": rundown_finished,
                }
            )

    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _ExtendedAutonomousGame()
    manager.register(game)
    await game.start()

    two_play_rundown = Rundown(
        rundown_id="two_play_notes",
        title="带备注双游玩段单",
        segments=[
            RundownSegment(
                id="play_1",
                title="第一段",
                task_description="搭营地",
                expected_ms=1_200_000,
                mode="play",
                notes="营地靠河建",
            ),
            RundownSegment(
                id="play_2",
                title="第二段",
                task_description="下矿",
                expected_ms=1_200_000,
                mode="play",
                notes="优先找铁和红石",
            ),
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": two_play_rundown.rundown_id, "lead_mode": "dynamic"},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(two_play_rundown),
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=904, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is True
        assert game.enter_contexts[0].segment_notes == "营地靠河建"
        assert game.enter_contexts[0].rundown_finished is False

        # 切到第二段：同步 segment_notes 且 rundown_finished=False
        ok_mid, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok_mid is True
        assert game.ext_updates[-1] == {
            "segment_id": "play_2",
            "segment_notes": "优先找铁和红石",
            "rundown_finished": False,
        }

        # 运营者手动在末段推进导致流程单 finish（segment is None）：显式发送 rundown_finished=True
        ok_fin, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok_fin is True
        assert game.ext_updates[-1] == {
            "segment_id": "",
            "segment_notes": "",
            "rundown_finished": True,
        }

        # 流程单已结束后若重新交权（如收权再交权），LeadHandoverContext.rundown_finished 为 True
        await lead_coord.reclaim_streamer_lead(reason="manual")
        await agent.sync_lead_mode(reason="re_enter_after_finish")
        assert game.enter_contexts[-1].rundown_finished is True
    finally:
        await agent.stop()
        await game.stop()


async def test_high_risk_attacked_triggers_mouth_in_game_lead_without_waking_planner(prompts: Any) -> None:
    """游戏自主主控态下：高危 game.body.attacked 放行触发 Mouth（独立长冷却桶）且不唤醒 Planner；轻摔伤静默。"""
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
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "mouth": {"enabled": True}},
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
            LiveStartedPayload(live_session_id=905, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 普通轻摔伤：只记背景不触发 Mouth
        await bus.emit(
            CoreEvents.GAME_BODY_ATTACKED,
            BodyEventPayload(game="adventure", kind="attacked", summary="摔了一下"),
            source="test",
        )
        await bus.wait_until_drained()
        mouth_stub.react.assert_not_called()

        # 高危受击（爆炸）：放行触发 Mouth，走独立冷却桶且不置位 game_pending 唤醒 Planner
        await bus.emit(
            CoreEvents.GAME_BODY_ATTACKED,
            BodyEventPayload(
                game="adventure",
                kind="attacked",
                summary="被苦力怕炸到一下",
                attacker="minecraft:creeper",
                hits=1,
            ),
            source="test",
        )
        await bus.wait_until_drained()
        mouth_stub.react.assert_called_once_with(
            HIGH_RISK_ATTACKED_KEY,
            "被苦力怕炸到一下",
            refresh_scene=False,
            kind="attacked",
        )
        assert agent._proactive_signals.game_pending is False
    finally:
        await agent.stop()
        await game.stop()


async def test_rundown_replace_definition_deleting_current_segment_does_not_emit_finished(prompts: Any) -> None:
    """运营者经 replace_definition 删除当前环节（index=-1 且未结束）时，不向游戏主控误发 rundown_finished=True。"""

    class _RecordingGame(_FakeAutonomousGameAgent):
        def __init__(self) -> None:
            super().__init__(ready=True)
            self.ext_updates: List[Dict[str, Any]] = []

        def receive_rundown_update(
            self,
            *,
            segment_id: str = "",
            segment_title: str = "",
            segment_goal: str = "",
            expected_ms: int = 0,
            segment_notes: str = "",
            rundown_finished: bool = False,
        ) -> None:
            super().receive_rundown_update(
                segment_id=segment_id,
                segment_title=segment_title,
                segment_goal=segment_goal,
                expected_ms=expected_ms,
            )
            self.ext_updates.append(
                {
                    "segment_id": segment_id,
                    "segment_notes": segment_notes,
                    "rundown_finished": rundown_finished,
                }
            )

    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game = _RecordingGame()
    manager.register(game)
    await game.start()

    original_rundown = Rundown(
        rundown_id="editable_play",
        title="可编辑游玩流程单",
        segments=[
            RundownSegment(id="play_1", title="第一段", task_description="搭营地", expected_ms=1_200_000, mode="play"),
            RundownSegment(id="play_2", title="第二段", task_description="探矿", expected_ms=1_200_000, mode="play"),
        ],
    )

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": original_rundown.rundown_id, "lead_mode": "dynamic"},
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(original_rundown),
        round_executor=FakeRoundExecutor(),
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=906, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        assert lead_coord.is_game_lead is True
        assert game.ext_updates == []

        # 运营者编辑流程单并删除当前所在的 play_1：游标退回 index=-1、current_segment 为 None 但 is_finished 为 False
        edited_rundown = Rundown(
            rundown_id="editable_play",
            title="可编辑游玩流程单",
            segments=[
                RundownSegment(
                    id="play_2", title="第二段", task_description="探矿", expected_ms=1_200_000, mode="play"
                ),
            ],
        )
        ok, _, _ = await agent.apply_rundown_definition(edited_rundown.model_dump())
        assert ok is True
        await bus.wait_until_drained()
        assert agent._rundown_coord.current_segment() is None
        assert agent._rundown_coord.is_finished() is False
        # 绝不能向游戏主控误发 rundown_finished=True
        assert game.ext_updates == []
        assert agent._proactive_signals.rundown_pending is False
    finally:
        await agent.stop()
        await game.stop()


@pytest.mark.parametrize(
    "scenario",
    ["streamer_only", "override_streamer", "no_candidates", "no_coordinator"],
)
async def test_rundown_change_to_play_segment_wakes_streamer_when_streamer_owns_lead(
    prompts: Any,
    scenario: str,
) -> None:
    """归主播承接的场次切到非 talk 段时（streamer_only / override==streamer / 无自主候选 / 未装配协调器），置位 rundown_pending 触发开场白。"""
    clock = _FakeClock()
    bus = EventBus()
    manager = AgentManager()
    game: Optional[_FakeAutonomousGameAgent] = None

    if scenario in ("streamer_only", "override_streamer"):
        game = _FakeAutonomousGameAgent(ready=True)
        manager.register(game)
        await game.start()

    mixed_rundown = Rundown(
        rundown_id=f"streamer_owned_{scenario}",
        title="主播承接游玩段流程单",
        segments=[
            RundownSegment(
                id="opening_talk", title="开场闲聊", task_description="打招呼", expected_ms=300_000, mode="talk"
            ),
            RundownSegment(
                id="play_1", title="自由游玩", task_description="一起探险", expected_ms=1_200_000, mode="play"
            ),
        ],
    )

    lead_coord = None if scenario == "no_coordinator" else LeadCoordinator(manager, event_bus=bus, clock=clock)
    if scenario == "override_streamer" and lead_coord is not None:
        await lead_coord.set_override("streamer")

    round_exec = FakeRoundExecutor()
    agent = make_streamer_agent(
        {
            "rundown_id": mixed_rundown.rundown_id,
            "lead_mode": "streamer_only" if scenario == "streamer_only" else "dynamic",
            "proactive": {"enabled": True},
        },
        prompt=prompts,
        event_bus=bus,
        rundown_repo=_FakeRundownRepo(mixed_rundown),
        round_executor=round_exec,
        lead_coordinator=lead_coord,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=907, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        # 消费开场 talk 段的信号，清空调用记录，聚焦验证切入 play 段的行为
        await agent._evaluate_proactive_round()
        round_exec.calls.clear()
        assert agent._proactive_signals.rundown_pending is False

        ok, _, _ = await agent.rundown_control("next", now_ms=clock())
        assert ok is True
        await bus.wait_until_drained()

        # 要么已置位 rundown_pending 等下一 tick 触发，要么后台循环已直接消费并产出 RUNDOWN 决策轮
        triggered_in_bg = any(call["trigger_cause"].kind is TriggerKind.RUNDOWN for call in round_exec.calls)
        if not triggered_in_bg:
            assert agent._proactive_signals.rundown_pending is True
            assert await agent._evaluate_proactive_round() is True
            assert any(call["trigger_cause"].kind is TriggerKind.RUNDOWN for call in round_exec.calls)
        if game is not None:
            assert game.rundown_updates == []
    finally:
        await agent.stop()
        if game is not None:
            await game.stop()


async def test_enter_welcome_stays_neutral_for_unknown_and_annotates_verified_viewer_and_player_facts(
    prompts: Any,
) -> None:
    """轻量进房欢迎移除硬编码「新观众」假前提：未知观众保持中性，仅查到真实到访记录或玩家别名时才附注真实身份。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_emitted = asyncio.Event()

    async def _on_speech(_name: str, _payload: StreamerSpeechPayload, _src: str) -> None:
        speech_emitted.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    replyer_calls: List[Dict[str, Any]] = []

    class _RecordingReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode
            return 70 if has_target else 30

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            replyer_calls.append(dict(kwargs))
            names = "、".join(kwargs.get("keep_names") or ())
            return {"speech": f"欢迎{names}来直播间坐坐呀！", "llm_request_id": f"req_ev_{len(replyer_calls)}"}

    class _FakeViewerRepo:
        def __init__(self, stats_by_id: Dict[str, ViewerPresenceStats]) -> None:
            self._stats = stats_by_id

        async def resolve_presence_stats(
            self, *, external_id: str = "", user_name: str = "", current_session_id: Optional[int] = None
        ) -> Optional[ViewerPresenceStats]:
            del user_name, current_session_id
            return self._stats.get(external_id)

    class _FakePlayerRepo:
        def __init__(self, players: List[GamePlayer]) -> None:
            self._players = players

        async def list_players(self, *, limit: int = 200) -> List[GamePlayer]:
            return self._players[:limit]

    class _FakePrivacyService:
        def __init__(self, forgotten_external_ids: set[str]) -> None:
            self._forgotten_external_ids = forgotten_external_ids

        async def is_player_forgotten(self, *, game: str, uuid: str) -> bool:
            del game, uuid
            return False

        async def is_external_id_forgotten(self, *, id_kind: str, external_id: str) -> bool:
            del id_kind
            return external_id in self._forgotten_external_ids

    viewer_repo = _FakeViewerRepo(
        {
            "u_old": ViewerPresenceStats(
                primary_viewer_id=10,
                cluster_viewer_ids=(10,),
                user_name="老友甲",
                earlier_sessions=4,
                prev_seen_ms=clock() - 86_400_000,
                forget_requested_at_ms=None,
                replied_count=2,
            ),
            "u_forget": ViewerPresenceStats(
                primary_viewer_id=11,
                cluster_viewer_ids=(11,),
                user_name="已忘乙",
                earlier_sessions=8,
                prev_seen_ms=clock() - 86_400_000,
                forget_requested_at_ms=clock() - 10_000,
                replied_count=6,
            ),
            # 申请遗忘的观众昵称与活跃游戏玩家同名：必须阻断跨表穿透，绝不附注游戏熟人身份
            "u_forget_same_name": ViewerPresenceStats(
                primary_viewer_id=12,
                cluster_viewer_ids=(12,),
                user_name="PlayerAlpha",
                earlier_sessions=5,
                prev_seen_ms=clock() - 86_400_000,
                forget_requested_at_ms=clock() - 20_000,
                replied_count=3,
            ),
        }
    )
    player_repo = _FakePlayerRepo(
        [
            GamePlayer(
                id=1,
                game="adventure",
                uuid="uuid-p1",
                name="PlayerAlpha",
                names=["PlayerAlpha"],
                aliases=["阿法"],
                first_seen_ms=100,
                last_seen_ms=200,
            ),
            GamePlayer(
                id=2,
                game="adventure",
                uuid="uuid-p2",
                name="ForgottenPlayer",
                names=["ForgottenPlayer"],
                aliases=["小忘"],
                first_seen_ms=100,
                last_seen_ms=300,
                forget_requested_at_ms=clock() - 5_000,
            ),
        ]
    )
    privacy_service = _FakePrivacyService({"u_forget_list_alias"})

    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "proactive": {"enabled": True}},
        prompt=prompts,
        replyer=_RecordingReplyer(),
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        viewer_repo=viewer_repo,
        game_player_repo=player_repo,
        privacy_service=privacy_service,
        enter_welcome_debounce_ms=0,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=908, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 无历史记录的陌生观众 + 已申请遗忘的观众（含与游戏玩家同名与命中不记录名单者）-> 全程零身份附注、绝不跨表穿透
        for uid, uname in (
            ("u_unknown", "路人丙"),
            ("u_forget", "已忘乙"),
            ("u_forget_same_name", "PlayerAlpha"),
            ("u_forget_list_alias", "阿法"),
        ):
            await bus.emit(
                CoreEvents.ROOM_MESSAGE_ENTER,
                RoomMessagePayload(
                    message_type="enter",
                    message_id=f"enter_{uid}",
                    content="",
                    user=RoomMessageUser(id=uid, name=uname),
                    timestamp_ms=clock(),
                ),
                source="test",
            )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        facts_neutral = replyer_calls[-1]["state_facts"]
        assert "刚进直播间待欢迎的观众：路人丙、已忘乙、PlayerAlpha、阿法" in facts_neutral
        assert "新观众" not in facts_neutral
        assert "已核实的观众身份事实" not in facts_neutral
        assert "游戏里的熟人" not in facts_neutral

        # 复位本批名字的已欢迎状态后再测未遗忘老观众与游戏玩家别名命中
        agent._room_state.reset_session()
        speech_emitted.clear()
        for uid, uname in (("u_old", "老友甲"), ("u_alias", "阿法"), ("u_fp", "小忘")):
            await bus.emit(
                CoreEvents.ROOM_MESSAGE_ENTER,
                RoomMessagePayload(
                    message_type="enter",
                    message_id=f"enter_{uid}",
                    content="",
                    user=RoomMessageUser(id=uid, name=uname),
                    timestamp_ms=clock(),
                ),
                source="test",
            )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        facts_verified = replyer_calls[-1]["state_facts"]
        assert "新观众" not in facts_verified
        assert "老友甲：来过多次的老观众" in facts_verified
        assert "阿法：游戏里的熟人（账号 PlayerAlpha，常叫：阿法）" in facts_verified
        assert "ForgottenPlayer" not in facts_verified and "小忘：" not in facts_verified
    finally:
        await agent.stop()
        await game.stop()


async def test_enter_welcome_injects_zero_io_activity_fact_and_omits_stale_activity(prompts: Any) -> None:
    """轻量进房欢迎注入零 I/O 当前活动只读快照（优先在跑步骤，其次近期解说摘要），过期旧活动省略且给出防编造边界。"""
    clock = _FakeClock()
    bus = EventBus()
    speech_emitted = asyncio.Event()

    async def _on_speech(_name: str, _payload: StreamerSpeechPayload, _src: str) -> None:
        speech_emitted.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _on_speech, model_class=StreamerSpeechPayload)

    manager = AgentManager()
    game = _FakeAutonomousGameAgent(ready=True)
    manager.register(game)
    await game.start()

    replyer_calls: List[Dict[str, Any]] = []

    class _RecordingReplyer:
        def budget_for(self, mode: str, *, has_target: bool) -> int:
            del mode
            return 70 if has_target else 30

        async def generate(self, **kwargs: Any) -> Dict[str, Any]:
            replyer_calls.append(dict(kwargs))
            names = "、".join(kwargs.get("keep_names") or ())
            if names:
                return {"speech": f"欢迎{names}来直播间呀！", "llm_request_id": f"req_act_{len(replyer_calls)}"}
            return {"speech": "刚回到地面准备整理仓库啦！", "llm_request_id": f"req_comm_{len(replyer_calls)}"}

    task_ledger = TaskLedger()
    lead_coord = LeadCoordinator(manager, event_bus=bus, clock=clock)
    agent = make_streamer_agent(
        {"rundown_id": GAME_OPENING_TALK_RUNDOWN.rundown_id, "proactive": {"enabled": True}},
        prompt=prompts,
        replyer=_RecordingReplyer(),
        round_executor=FakeRoundExecutor(),
        event_bus=bus,
        task_ledger=task_ledger,
        lead_coordinator=lead_coord,
        rundown_repo=_FakeRundownRepo(GAME_OPENING_TALK_RUNDOWN),
        enter_welcome_debounce_ms=0,
        clock=clock,
    )
    manager.register(agent)
    await agent.start()

    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=909, kind="live", started_at_ms=clock()),
            source="test",
        )
        await bus.wait_until_drained()
        await agent.rundown_control("next", now_ms=clock())
        assert lead_coord.is_game_lead is True

        # 场景 1：游戏主控在任务账本中有正在运行的步骤（含坐标与内部编号）-> 脱敏后写入当前游戏活动事实
        task_ledger.register(
            task_id="step_1",
            provider=game.name,
            tool="move",
            initiator=game.name,
            executor=game.name,
            source="provider",
            status="running",
            snapshot={"purpose": "前往 (-120, 64, 88) 整理仓库箱子 goal_id=12"},
        )
        await bus.emit(
            CoreEvents.ROOM_MESSAGE_ENTER,
            RoomMessagePayload(
                message_type="enter",
                message_id="enter_act_1",
                content="",
                user=RoomMessageUser(id="viewer_a", name="观众甲"),
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        facts_step = replyer_calls[-1]["state_facts"]
        assert "当前游戏活动：前往那里整理仓库箱子" in facts_step
        assert "(-120, 64, 88)" not in facts_step and "goal_id" not in facts_step
        assert "未给出时只做欢迎不补充活动细节" in facts_step

        # 场景 2：在跑步骤结束，但刚收到一条近期实况解说 -> 回退到近期实况解说摘要
        task_ledger.update("step_1", "succeeded")
        clock.advance(5_000)
        ack = await lead_coord.submit_commentary(
            game.name,
            CommentaryRequest(
                game=game.name,
                summary="刚回到地面营地准备扩建温室",
                urgency="normal",
                timestamp_ms=clock(),
            ),
        )
        assert ack.accepted is True

        speech_emitted.clear()
        clock.advance(10_000)
        await bus.emit(
            CoreEvents.ROOM_MESSAGE_ENTER,
            RoomMessagePayload(
                message_type="enter",
                message_id="enter_act_2",
                content="",
                user=RoomMessageUser(id="viewer_b", name="观众乙"),
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        facts_commentary = replyer_calls[-1]["state_facts"]
        assert "当前游戏活动：刚回到地面营地准备扩建温室" in facts_commentary

        # 场景 3：13 分钟后没有新的在跑步骤也没有新的近期活动 -> 省略过期旧活动，仅保留防编造边界
        speech_emitted.clear()
        clock.advance(13 * 60 * 1000)
        await bus.emit(
            CoreEvents.ROOM_MESSAGE_ENTER,
            RoomMessagePayload(
                message_type="enter",
                message_id="enter_act_3",
                content="",
                user=RoomMessageUser(id="viewer_c", name="观众丙"),
                timestamp_ms=clock(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await asyncio.wait_for(speech_emitted.wait(), timeout=2.0)
        await bus.wait_until_drained()

        facts_stale = replyer_calls[-1]["state_facts"]
        assert "当前游戏活动：" not in facts_stale
        assert "扩建温室" not in facts_stale
        assert "未给出时只做欢迎不补充活动细节" in facts_stale
    finally:
        await agent.stop()
        await game.stop()
