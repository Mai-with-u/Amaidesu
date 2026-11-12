"""决策轮执行器（两阶段决策的执行半）。

调度半（flush 循环 + MessageBuffer + ProactiveTrigger + pending 标志 +
``_flush_lock``）留在 StreamerAgent；本组件只负责"拿到一批就执行一轮"：
生成 round_id → 发射 ``streamer.stage``（planning）→ Planner ReAct 决策
→ 产出送发言管线 → 发射 ``planner.decision`` 与 idle 状态。

依赖全部构造期注入（不持 Agent 引用）；决策窗材料经 ``window_opener``
一次取齐（出账与读取的顺序不变量内化在 SessionScope.open_window），
流程单文本与运营提醒这两个非场次作用域项仍经 provider 回调自取。

收播道别那一轮由调用方带上正在结束的场次（``ClosingSession``）：开窗按这一场
读历史，道别句带这一场的编号送发言管线；其余轮次照常交给场次解析。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import (
    PlannerBatchItem,
    PlannerDecisionPayload,
    StreamerStagePayload,
)
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import ModuleLogger, get_logger
from src.modules.utils.time_utils import now_ms

from src.agents.streamer import canonical

from .mouth_reconcile import ReconcileVerdict
from .planner import Planner, RoundOutcome
from .proactive_trigger import ProactiveTrigger
from .room_state import RoomState
from .session_scope import ClosingSession, WindowMaterials
from .speech_dispatcher import SpeechDispatcher
from .stats import StreamerStats
from .thinking_stream import ThinkingStreamContext
from .trigger_cause import TriggerCause, TriggerKind

__all__ = [
    "DecisionRoundExecutor",
    "WindowOpener",
    "resolve_reply_message_id",
    "resolve_reply_target_user",
]

# 模块级 logger：反查 reply target 的异常路径记 warning 用（组件实例可带注入 logger）
_MODULE_LOGGER = get_logger("StreamerAgent.DecisionRoundExecutor")


class WindowOpener(Protocol):
    """开一个决策窗的入口（``SessionScope.open_window`` 的形状）。

    ``scope_pk`` 为 None 时按进行中场次读历史；收播道别那一轮传正在结束的那一场。
    """

    def __call__(self, *, scope_pk: Optional[int] = None) -> Awaitable[WindowMaterials]: ...


#: 触发原因种类（TriggerKind 全集）→ 面板可读文案。
#: 机器可读形态由 TriggerCause.to_reason_string 派生（planner.decision.trigger_reason
#: 与日志），此处只译展示文本。
_TRIGGER_KIND_LABEL: Dict[TriggerKind, str] = {
    TriggerKind.FORCED: "付费触发（SC / 礼物 / 上舰）",
    TriggerKind.BATCH_FULL: "弹幕攒满一批",
    TriggerKind.WINDOW_EXPIRED: "聚合窗口到期",
    TriggerKind.IDLE_COMPENSATION: "冷场补足一批",
    TriggerKind.EXTERNAL: "外部指令唤醒",
    TriggerKind.SCHEDULE: "定时主动开麦",
    TriggerKind.COLD: "冷场主动开麦",
    TriggerKind.REMINDER: "递话催醒",
    TriggerKind.RUNDOWN: "流程单推进",
    TriggerKind.GAME: "游戏 Agent 待定夺",
    TriggerKind.SIGNOFF: "收播道别",
    TriggerKind.RESUME: "断线续播",
    TriggerKind.DASHBOARD_DEBUG: "控制台决策测试",
}

#: 静默原因（Planner outcome.silent_reason）→ 面板可读文案。
_SILENT_REASON_LABEL: Dict[str, str] = {
    "natural": "自然终止",
    "max_steps": "超出步数上限",
    "llm_error": "LLM 调用异常",
    "llm_failed": "LLM 返回失败",
    "prompt_render_failed": "提示词渲染失败",
    "assembler_failed": "上下文组装失败",
    "session_ended": "开窗时的场次已下播",
    "reply_held": "给游戏的安排被拒，那句话压住后没再开口",
    "memory_repeat_held": "点名本场提过的旧事被退回后没再开口",
    "mouth_covered": "想说的内容刚才嘴已经替她说过，本轮不再重复",
    "mouth_superseded": "构思期间现场已变且嘴已先回应，旧台词作废",
}


def trigger_cause_text(cause: TriggerCause, *, proactive: bool) -> str:
    """触发原因 → 面板可读文案。

    未知种类原样返回枚举值：新增触发源时面板先显示原值，据此补齐映射，
    不静默丢信息。控制台调试原因随本轮是否主动发言窗分化文案（主动调试
    窗是"手动触发一轮主动发言"，普通测试窗是决策测试）。
    """
    if cause.kind is TriggerKind.DASHBOARD_DEBUG and proactive:
        return "控制台手动触发"
    return _TRIGGER_KIND_LABEL.get(cause.kind, cause.kind.value)


def _silent_reason_text(silent_reason: str) -> str:
    """静默原因码 → 面板可读文案（未知码原样返回）。"""
    return _SILENT_REASON_LABEL.get(silent_reason, silent_reason)


class DecisionRoundExecutor:
    """一轮两阶段决策的执行体（批次 + 信号 → 决策结果视图）。"""

    def __init__(
        self,
        *,
        planner: Planner,
        speech: SpeechDispatcher,
        event_bus: Optional[EventBus],
        room_state: RoomState,
        proactive_trigger: ProactiveTrigger,
        stats: StreamerStats,
        thinking_sink: Optional[Any],
        thinking_enabled: bool,
        window_opener: WindowOpener,
        rundown_text_provider: Callable[[], Optional[str]],
        reminders_provider: Optional[Callable[[], str]] = None,
        resume_replay_provider: Optional[Callable[[], str]] = None,
        speech_allowed: Optional[Callable[[], bool]] = None,
        mouth_reconciler: Optional[Callable[[str, int], Awaitable[ReconcileVerdict]]] = None,
        on_mouth_superseded: Optional[Callable[[], None]] = None,
        logger: Optional[ModuleLogger] = None,
    ) -> None:
        """决策窗材料的取用契约：

        - window_opener: 开一个决策窗，一次取齐场次作用域材料（出账先于读取，
          顺序不变量内化在 ``SessionScope.open_window``）。每轮以关键字
          ``scope_pk`` 调用：常规轮次为 None，收播道别那一轮为正在结束的场次号。
          返回的 ``WindowMaterials`` 字段语义见其类型注释——``history`` 的 ``None``
          与空列表不同义，``game_batch`` 空批次即无事件，``timeline_notes`` 空列表
          即本窗没有新的游戏动作记录。
        - rundown_text: ``Optional[str]``（None = 无流程单情境；非场次作用域
          项，经流程单协调器自取）。
        - reminders: ``str``（可为空串；运营递话留言，**读取即取空队列**——
          送达一次制，缺省 = 不注入）。
        - speech_allowed: 本轮说完时还能不能出声（缺省 = 一律能）。Agent 按"开窗时所在
          的场次是否已下播"回答：下播后只有道别那一轮能开口，下播前开窗、下播后才拿到
          结果的常规轮作废，免得道别句之后又冒一句、还被记进这一场。
        """
        self._logger = logger or get_logger("StreamerAgent.DecisionRoundExecutor")
        self._planner = planner
        self._speech = speech
        self._event_bus = event_bus
        self._room_state = room_state
        self._proactive_trigger = proactive_trigger
        self._stats = stats
        self._thinking_sink = thinking_sink
        self._thinking_enabled = thinking_enabled
        self._window_opener = window_opener
        self._rundown_text_provider = rundown_text_provider
        self._reminders_provider = reminders_provider
        # 续播重放玩家聊天（重启前她可能还没回的那几句）的一次性来源（读取即清空——注入一次制；缺省 = 不注入）
        self._resume_replay_provider = resume_replay_provider
        self._speech_allowed = speech_allowed
        self._mouth_reconciler = mouth_reconciler
        self._on_mouth_superseded = on_mouth_superseded
        # 决策轮次自增计数器（round_id 生成用；进程内单调）
        self._round_seq: int = 0

    def set_reminders_provider(self, fn: Callable[[], str]) -> None:
        """接线运营递话来源：注入方经此方法提供取空动词，与构造参数通道同写一处。"""
        self._reminders_provider = fn

    def set_resume_replay_provider(self, fn: Callable[[], str]) -> None:
        """接线续播重放玩家聊天来源：注入方经此方法提供取空动词（构造参数通道同写一处）。"""
        self._resume_replay_provider = fn

    async def execute(
        self,
        batch: List[RoomMessagePayload],
        *,
        forced: bool,
        trigger_cause: TriggerCause,
        proactive: bool = False,
        closing_session: Optional[ClosingSession] = None,
    ) -> Dict[str, Any]:
        """两阶段决策外壳：Planner → 消费 plan 评估 + 触发 reply 工具。

        决策可观测收口（每轮恰好一条 planner.decision，成功/失败/降级全覆盖）：
        生成本轮 ``round_id``，边界处发射 ``streamer.stage``（planning → idle），
        决策收口处发射 ``planner.decision``——观察者据此回答"主播为什么这么做/
        为什么没反应"，无需翻日志。两阶段执行逻辑在 ``_decide_round``。

        ``closing_session`` 只在收播道别那一轮给出：开窗按这一场读历史、道别句
        带这一场的编号，她道别时看得到本场最后的对话，说的话也记在这一场。

        Returns:
            决策结果视图（正常 flush 循环忽略返回值；调试门面
            ``debug_test_decision`` 消费它向 Dashboard 回传完整中间产物）::

                {
                    "round_id": str,
                    "trigger_reason": str,
                    "proactive": bool,
                    "forced": bool,
                    "plan": {
                        "should_reply",
                        "target",
                        "reply_to",
                        "topic_summary",
                        "topic_tag",
                        "reply_guidance",
                        "silent_reason",
                    }
                    | None,
                    "speech": str | None,
                    "emotion": str | None,
                    "utterance_id": str | None,
                    "reply_to_message_id": str | None,  # 回复关联键
                    "silent_reason": str | None,  # 静默原因码（natural/max_steps/llm_error 等）
                    "error": str | None,  # planner/reply 失败原因，成功为 None
                    "planner_raw": str,  # Planner LLM 原始输出（完整内容）
                    "llm_request_id": str | None,  # LLM 请求历史指针
                    "planner_duration_ms": int,
                    "reply_duration_ms": int,
                    "total_duration_ms": int,
                }
        """
        round_id = self._next_round_id()
        started_ms = now_ms()
        await self._emit_streamer_stage(
            stage="planning",
            agent_state="running",
            round_id=round_id,
            detail=trigger_cause_text(trigger_cause, proactive=proactive),
        )
        result, outcome = await self._decide_round(
            batch,
            round_id=round_id,
            started_ms=started_ms,
            forced=forced,
            trigger_cause=trigger_cause,
            proactive=proactive,
            closing_session=closing_session,
        )
        await self._emit_planner_decision(result, outcome, batch)
        closing = "决策轮结束"
        if result.get("speech"):
            closing += "：发言已出"
        elif result.get("error"):
            closing += f"：{result['error']}"
        elif result.get("silent_reason"):
            closing += f"：静默（{_silent_reason_text(str(result['silent_reason']))}）"
        await self._emit_streamer_stage(
            stage="idle",
            agent_state="wait",
            round_id=round_id,
            detail=closing,
        )
        return result

    async def _decide_round(
        self,
        batch: List[RoomMessagePayload],
        *,
        round_id: str,
        started_ms: int,
        forced: bool,
        trigger_cause: TriggerCause,
        proactive: bool,
        closing_session: Optional[ClosingSession] = None,
    ) -> Tuple[Dict[str, Any], Optional[RoundOutcome]]:
        """两阶段决策执行体（被 ``execute`` 外壳驱动），返回（结果视图, Planner 收场）。

        决策过程副产品（原始输出/请求指针/分段耗时）随 ``result`` 带回，由外壳
        统一发射决策事件，本方法不直接发事件。Planner 收场（RoundOutcome）单独
        带回供决策事件字段直取；Planner 调用异常收场时为 None。
        """
        # 收播道别那一轮：场次已写成已结束，"当前场次"解析不到，按正在结束的那一场读历史、
        # 给道别句盖号；其余轮次为 None / 0，照常交给场次解析与盖章拦截器
        scope_pk = closing_session.live_session_id if closing_session is not None else None
        result: Dict[str, Any] = {
            "round_id": round_id,
            "trigger_reason": trigger_cause.to_reason_string(proactive=proactive),
            "proactive": proactive,
            "forced": forced,
            "plan": None,
            "speech": None,
            "emotion": None,
            "utterance_id": None,
            "reply_to_message_id": None,
            "silent_reason": None,
            "error": None,
            "planner_raw": "",
            "llm_request_id": None,
            "planner_duration_ms": 0,
            "reply_duration_ms": 0,
            "total_duration_ms": now_ms() - started_ms,
        }

        # 开决策窗：出账 + 读历史 + 本窗批次 + 时间线 notes 一次取齐（顺序不变量
        # 在 SessionScope.open_window 内化——出账的时间线行必先进本轮投影）
        window_opened_ms = now_ms()
        materials = await self._window_opener(scope_pk=scope_pk)
        history = materials.history

        # 拼装流程单上下文
        rundown_text = self._rundown_text_provider()

        # 运营递话留言（读取即取空队列——送达一次制；缺省不注入）
        reminders = self._reminders_provider() if self._reminders_provider else ""
        # 续播重放玩家聊天（读取即清空——注入一次制；缺省不注入）
        resume_replay = self._resume_replay_provider() if self._resume_replay_provider else ""

        # Planner ReAct 决策（循环内完成查信息与 reply 调用；失败细节经
        # outcome.error 带出，供决策事件区分降级原因）
        planner_started_ms = now_ms()
        thinking = None
        if self._thinking_sink is not None and self._thinking_enabled:
            thinking = ThinkingStreamContext(self._thinking_sink, round_id)
        try:
            outcome = await self._planner.plan(
                batch,
                forced=forced,
                proactive=proactive,
                history=history,
                rundown_text=rundown_text,
                game_batch=materials.game_batch,
                reminders=reminders,
                resume_replay=resume_replay,
                thinking=thinking,
                round_id=round_id,
                trigger_cause=trigger_cause,
            )
        except Exception as exc:
            self._logger.exception(f"Planner 调用异常: {exc}")
            outcome = None
            result["error"] = f"planner_failed: {exc}"
        result["planner_duration_ms"] = now_ms() - planner_started_ms
        # 原始输出与请求指针随 outcome 带回（plan() 的返回值契约）；异常收场时没有
        # outcome，保持空值——异常本身已写进 result.error
        result["planner_raw"] = outcome.raw_content if outcome is not None else ""
        result["llm_request_id"] = outcome.llm_request_id if outcome is not None else None

        if outcome is None:
            self._stats.planner_failures += 1
            self._stats.total_no_action += 1
            result["total_duration_ms"] = now_ms() - started_ms
            return result, None

        result["plan"] = {
            "should_reply": outcome.replied,
            "target": outcome.target,
            "reply_to": outcome.reply_to,
            "topic_summary": outcome.topic_summary,
            "topic_tag": outcome.topic_tag,
            "reply_guidance": outcome.reply_guidance,
            "silent_reason": outcome.silent_reason,
        }
        # 回复关联键服务端解析：planner 给的指向是自由文本，落库前先解析成
        # 本轮批内的真 message_id（解析不中就为 None，宁缺毋假）
        result["reply_to_message_id"] = resolve_reply_message_id(outcome, batch)
        result["silent_reason"] = outcome.silent_reason
        result["reply_duration_ms"] = outcome.reply_duration_ms

        # 未说话（自然终止/超步/LLM 失败/reply 工具失败）——静默收场
        if not outcome.replied:
            self._stats.total_no_action += 1
            self._stats.replyer_failures += outcome.reply_failures
            if outcome.error:
                self._stats.planner_failures += 1
                result["error"] = f"planner_failed: {outcome.error}"
            if outcome.silent_reason == "mouth_superseded" and self._on_mouth_superseded is not None:
                self._on_mouth_superseded()
            result["total_duration_ms"] = now_ms() - started_ms
            return result, outcome

        # 开窗时所在的场次已经下播（常规轮跑到一半，运营结束了场次）：这句不送发言管线。
        # 道别那一轮与它并发，往往先说完"大家晚安"，这句再出声就排在道别句后面，
        # 还会记进这一场、被下播整理当成她最后说的话写进档案
        if self._speech_allowed is not None and not self._speech_allowed():
            self._logger.info(f"开窗时的场次已下播，本轮台词作废不出声: round_id={round_id}")
            self._stats.total_no_action += 1
            result["silent_reason"] = "session_ended"
            result["plan"]["silent_reason"] = "session_ended"
            result["total_duration_ms"] = now_ms() - started_ms
            return result, outcome

        # 出声前第二道关口：台词生成完后等 TTS 播放空档的十几秒里，嘴可能刚插话回应了新事件或欢迎了同一个人；
        # 占住播放槽位后、发出 streamer.speech 前补核对这期间的新嘴话，重复或矛盾即撤回
        veto_reason: Optional[str] = None
        check_since_ms = outcome.mouth_checked_ms or window_opened_ms
        reconciler = self._mouth_reconciler

        async def _before_emit(candidate_speech: str) -> Optional[str]:
            nonlocal veto_reason
            assert reconciler is not None  # noqa: S101
            verdict = await reconciler(candidate_speech, check_since_ms)
            if verdict.action == "pass":
                return candidate_speech
            if verdict.action == "trim":
                return verdict.speech
            veto_reason = "mouth_superseded" if verdict.action == "contradicted" else "mouth_covered"
            return None

        # reply 已在 Planner ReAct 循环内经 reply 工具完成（Planner 阶段耗时含
        # 表达生成）；此处仅把产出送发言管线（speech → TTS / emotion → VTS）。
        # await 保证 streamer.speech 先于本轮 planner.decision 与 idle 状态发出
        speech_info = await self._speech.dispatch(
            outcome.reply_payload,
            target_user_id=resolve_reply_target_user(outcome, batch),
            reply_to_message_id=result["reply_to_message_id"],
            round_id=round_id,
            live_session_id=scope_pk if scope_pk is not None else 0,
            memory_refs=list(outcome.memory_refs),
            reply_player=outcome.reply_player,
            before_emit=_before_emit if reconciler is not None else None,
        )
        if veto_reason is not None:
            self._logger.info(f"等播放空档期间嘴已先开口，出声前撤回本轮台词（{veto_reason}）: round_id={round_id}")
            self._stats.total_no_action += 1
            result["silent_reason"] = veto_reason
            result["plan"]["should_reply"] = False
            result["plan"]["silent_reason"] = veto_reason
            outcome = replace(outcome, replied=False, silent_reason=veto_reason, speech=None)
            if veto_reason == "mouth_superseded" and self._on_mouth_superseded is not None:
                self._on_mouth_superseded()
            result["total_duration_ms"] = now_ms() - started_ms
            return result, outcome
        if speech_info is not None:
            result["speech"], result["emotion"], result["utterance_id"] = speech_info

        # 成功：保存上下文 + 记录发言时刻 + 频率限制
        self._stats.total_replies += 1
        self._room_state.record_speech(now_ms())
        if proactive:
            self._proactive_trigger.record_trigger(trigger_cause.kind, now_ms())

        result["total_duration_ms"] = now_ms() - started_ms
        return result, outcome

    def _next_round_id(self) -> str:
        """生成下一个决策轮次 ID（格式 ``rnd_{epoch_ms}_{seq}``）。

        与 utterance_id 同风格的进程内单调关联键：本轮弹幕批次、决策记录、
        发言、工具结果经它成组（观察器按轮渲染）。
        """
        self._round_seq += 1
        return f"rnd_{now_ms()}_{self._round_seq}"

    async def _emit_streamer_stage(
        self,
        *,
        stage: str,
        agent_state: str,
        round_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        """发布 ``streamer.stage`` 阶段状态事件（决策循环内直接 await，保证先后顺序）。"""
        if self._event_bus is None:
            return
        payload = StreamerStagePayload(
            stage=stage,
            agent_state=agent_state,
            round_id=round_id,
            detail=detail,
        )
        try:
            await self._event_bus.emit(
                CoreEvents.STREAMER_STAGE,
                payload,
                source="streamer_agent.stage",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 观测事件不阻断决策循环
            self._logger.warning(f"streamer.stage 发布失败（已忽略）: stage={stage}, err={exc}")

    async def _emit_planner_decision(
        self,
        result: Dict[str, Any],
        outcome: Optional[RoundOutcome],
        batch: List[RoomMessagePayload],
    ) -> None:
        """发布 ``planner.decision`` 决策轮记录事件（决策循环内直接 await，保证先于 idle 状态）。

        决策结论字段从 Planner 收场（RoundOutcome）直取；触发原因、批次摘要、
        发言产出、失败原因与分段耗时取自结果视图，全部入事件，观察者与互动
        分析不再依赖日志。Planner 调用异常收场（outcome 为 None）时决策结论
        按未说话处理，失败原因已在 result.error。
        """
        if self._event_bus is None:
            return
        # Planner 调用异常收场时没有收场对象，决策结论按默认值（未说话）入事件
        plan_view = outcome if outcome is not None else RoundOutcome()
        # 关联/指针字段统一收口为 str（鸭子类型 mock 响应可能带任意对象属性）
        request_id = result.get("llm_request_id")
        reply_to = result.get("reply_to_message_id")
        silent = result.get("silent_reason")
        payload = PlannerDecisionPayload(
            round_id=str(result.get("round_id") or ""),
            trigger_reason=str(result.get("trigger_reason") or ""),
            proactive=bool(result.get("proactive")),
            forced=bool(result.get("forced")),
            batch=[
                PlannerBatchItem(
                    message_id=str(getattr(msg, "message_id", "") or ""),
                    user_id=str(getattr(msg.user, "id", "") or ""),
                    user_name=str(getattr(msg.user, "name", "") or ""),
                    text=str(getattr(msg, "content", "") or ""),
                )
                for msg in batch or []
            ],
            should_reply=plan_view.replied,
            target=(str(plan_view.target) if plan_view.target else None),
            topic_summary=str(plan_view.topic_summary or ""),
            topic_tag=str(plan_view.topic_tag or ""),
            reply_guidance=str(plan_view.reply_guidance or ""),
            reply_to_message_id=str(reply_to) if reply_to else None,
            silent_reason=str(silent) if silent else None,
            speech=(str(result["speech"]) if result.get("speech") else None),
            emotion=(str(result["emotion"]) if result.get("emotion") else None),
            utterance_id=(str(result["utterance_id"]) if result.get("utterance_id") else None),
            error=(str(result["error"]) if result.get("error") else None),
            planner_raw=str(result.get("planner_raw", "") or ""),
            llm_request_id=str(request_id) if request_id else None,
            planner_duration_ms=int(result.get("planner_duration_ms", 0) or 0),
            reply_duration_ms=int(result.get("reply_duration_ms", 0) or 0),
            total_duration_ms=int(result.get("total_duration_ms", 0) or 0),
        )
        try:
            await self._event_bus.emit(
                CoreEvents.PLANNER_DECISION,
                payload,
                source="streamer_agent.decision",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 观测事件不阻断决策循环
            self._logger.warning(f"planner.decision 发布失败（已忽略）: round_id={result.get('round_id')}, err={exc}")


def resolve_reply_target_user(
    outcome: RoundOutcome,
    batch: List[RoomMessagePayload],
) -> Optional[str]:
    """从 batch 反查本次回复的观众 user_id。

    优先消费 ``outcome.reply_to``（reply 意图指向的弹幕 message_id——按
    message_id 等值命中即可）；未提供时回退 ``outcome.target``（弹幕
    ``message_id`` 或文本片段）匹配：message_id 等值 → text 包含/相等。
    全未命中时保守兜底为 batch 最后一条消息的 user_id（"回复最后那条"
    通常是意图所指）；batch 为空或 target 为空时返回 None。只做反查，
    不写状态、不发事件；异常吞掉记 warning 不上抛（决策循环必须继续）。
    """
    reply_to = outcome.reply_to
    target = reply_to or outcome.target
    if not isinstance(target, str) or not target:
        return None
    if not batch:
        return None

    try:
        # reply_to 是精确 message_id，等值命中即返回
        if reply_to:
            for msg in batch:
                if getattr(msg, "message_id", None) == reply_to:
                    return getattr(msg.user, "id", None)
            return None
        for msg in batch:
            if getattr(msg, "message_id", None) == target:
                return getattr(msg.user, "id", None)
            msg_text = getattr(msg, "content", None)
            if isinstance(msg_text, str) and msg_text and (target in msg_text or msg_text == target):
                return getattr(msg.user, "id", None)
        return getattr(batch[-1].user, "id", None)
    except Exception as exc:
        _MODULE_LOGGER.warning(f"反查 reply target user 异常: {exc}")
        return None


def resolve_reply_message_id(
    outcome: RoundOutcome,
    batch: List[RoomMessagePayload],
) -> Optional[str]:
    """把 planner 给的回复指向（reply_to / target）解析为本轮批内真 message_id。

    reply_to / target 是 planner LLM 抄自 canonical 行尾 ``[id:…]`` 的自由文本，
    可能是消息 ID、``[id:…]`` 引用写法、也可能是"用户名: 内容"整行展示串——
    直接落库会让前端反查（按 message_id 等值）拿不到对应弹幕。这里在落库前
    解析，两档匹配：target 恰为批内某条弹幕的 message_id 时直用；"用户名: 内容"
    形态按用户名精确匹配、且该用户本轮仅一条弹幕时取那条。其余一律返回 None
    （宁缺毋假——存错关联键比不存更糟）。只做解析，不写状态、不发事件；
    异常记 warning 不上抛（决策循环必须继续）。
    """
    reply_to = outcome.reply_to
    target = reply_to or outcome.target
    if not isinstance(target, str) or not target:
        return None
    if not batch:
        return None

    try:
        # ``[id:…]`` / ``id:…`` 引用写法归一成裸消息 ID（截断的 "[id:xxx" 也能归一）
        message_id = canonical.normalize_message_id(target).strip()
        if message_id and any(getattr(msg, "message_id", None) == message_id for msg in batch):
            return message_id
        # "用户名: 内容" 形态：用户名精确匹配，该用户本轮仅一条弹幕才取
        user, sep, _ = target.partition(":")
        if not sep:
            user, sep, _ = target.partition("：")
        user_name = user.strip()
        if not user_name:
            return None
        hits = [msg for msg in batch if getattr(getattr(msg, "user", None), "name", None) == user_name]
        if len(hits) == 1:
            hit_id = getattr(hits[0], "message_id", None)
            return hit_id if isinstance(hit_id, str) and hit_id else None
        return None
    except Exception as exc:
        _MODULE_LOGGER.warning(f"解析回复关联键异常: {exc}")
        return None
