"""决策轮执行器（两阶段决策的执行半）。

调度半（flush 循环 + MessageBuffer + ProactiveTrigger + pending 标志 +
``_flush_lock``）留在 StreamerAgent；本组件只负责"拿到一批就执行一轮"：
生成 round_id → 发射 ``streamer.stage``（planning）→ Planner ReAct 决策
→ 产出送发言管线 → 发射 ``planner.decision`` 与 idle 状态。

依赖全部构造期注入（不持 Agent 引用）；决策窗材料经 ``window_opener``
一次取齐（出账与读取的顺序不变量内化在 SessionScope.open_window），
流程单文本与运营提醒这两个非场次作用域项仍经 provider 回调自取。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

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

from .planner import Planner, RoundOutcome
from .proactive_trigger import ProactiveTrigger
from .room_state import RoomState
from .session_scope import WindowMaterials
from .speech_dispatcher import SpeechDispatcher
from .stats import StreamerStats
from .thinking_stream import ThinkingStreamContext
from .trigger_cause import TriggerCause, TriggerKind

__all__ = ["DecisionRoundExecutor"]


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
        window_opener: Callable[[], Awaitable[WindowMaterials]],
        rundown_text_provider: Callable[[], Optional[str]],
        reminders_provider: Optional[Callable[[], str]] = None,
        logger: Optional[ModuleLogger] = None,
    ) -> None:
        """决策窗材料的取用契约：

        - window_opener: 开一个决策窗，一次取齐场次作用域材料（出账先于读取，
          顺序不变量内化在 ``SessionScope.open_window``）。返回的
          ``WindowMaterials`` 字段语义见其类型注释——``history`` 的 ``None``
          与空列表不同义，``game_batch`` 空批次即无事件，``session_recap``
          空串即不注入。
        - rundown_text: ``Optional[str]``（None = 无流程单情境；非场次作用域
          项，经流程单协调器自取）。
        - reminders: ``str``（可为空串；运营递话留言，**读取即取空队列**——
          送达一次制，缺省 = 不注入）。
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
        # 决策轮次自增计数器（round_id 生成用；进程内单调）
        self._round_seq: int = 0

    async def execute(
        self,
        batch: List[RoomMessagePayload],
        *,
        forced: bool,
        trigger_cause: TriggerCause,
        proactive: bool = False,
    ) -> Dict[str, Any]:
        """两阶段决策外壳：Planner → 消费 plan 评估 + 触发 reply 工具。

        决策可观测收口（每轮恰好一条 planner.decision，成功/失败/降级全覆盖）：
        生成本轮 ``round_id``，边界处发射 ``streamer.stage``（planning → idle），
        决策收口处发射 ``planner.decision``——观察者据此回答"主播为什么这么做/
        为什么没反应"，无需翻日志。两阶段执行逻辑在 ``_decide_round``。

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
    ) -> Tuple[Dict[str, Any], Optional[RoundOutcome]]:
        """两阶段决策执行体（被 ``execute`` 外壳驱动），返回（结果视图, Planner 收场）。

        决策过程副产品（原始输出/请求指针/分段耗时）随 ``result`` 带回，由外壳
        统一发射决策事件，本方法不直接发事件。Planner 收场（RoundOutcome）单独
        带回供决策事件字段直取；Planner 调用异常收场时为 None。
        """
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

        # 开决策窗：出账 + 读历史 + 本窗批次 + 上场回顾一次取齐（顺序不变量
        # 在 SessionScope.open_window 内化——出账的时间线行必先进本轮投影）
        materials = await self._window_opener()
        history = materials.history

        # 拼装流程单上下文
        rundown_text = self._rundown_text_provider()

        # 运营递话留言（读取即取空队列——送达一次制；缺省不注入）
        reminders = self._reminders_provider() if self._reminders_provider else ""

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
                thinking=thinking,
                round_id=round_id,
                trigger_cause=trigger_cause,
                session_recap=materials.session_recap,
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
            "reply_guidance": outcome.reply_guidance,
            "silent_reason": outcome.silent_reason,
        }
        result["reply_to_message_id"] = outcome.reply_to
        result["silent_reason"] = outcome.silent_reason
        result["reply_duration_ms"] = outcome.reply_duration_ms

        # 未说话（自然终止/超步/LLM 失败/reply 工具失败）——静默收场
        if not outcome.replied:
            self._stats.total_no_action += 1
            self._stats.replyer_failures += outcome.reply_failures
            if outcome.error:
                self._stats.planner_failures += 1
                result["error"] = f"planner_failed: {outcome.error}"
            result["total_duration_ms"] = now_ms() - started_ms
            return result, outcome

        # reply 已在 Planner ReAct 循环内经 reply 工具完成（Planner 阶段耗时含
        # 表达生成）；此处仅把产出送发言管线（speech → TTS / emotion → VTS）。
        # await 保证 streamer.speech 先于本轮 planner.decision 与 idle 状态发出
        speech_info = await self._speech.dispatch(
            outcome.reply_payload,
            target_user_id=self._resolve_reply_target_user(outcome, batch),
            reply_to_message_id=outcome.reply_to,
            round_id=round_id,
        )
        if speech_info is not None:
            result["speech"], result["emotion"], result["utterance_id"] = speech_info

        # 成功：保存上下文 + 记录发言时刻 + 频率限制
        self._stats.total_replies += 1
        self._room_state.record_speech(now_ms())
        if proactive:
            self._proactive_trigger.record_trigger(trigger_cause.kind, now_ms())

        result["total_duration_ms"] = now_ms() - started_ms
        return result, outcome

    def _resolve_reply_target_user(
        self,
        outcome: RoundOutcome,
        batch: List[RoomMessagePayload],
    ) -> Optional[str]:
        """从 batch 反查本次回复的观众 user_id。

        优先消费 ``outcome.reply_to``（reply 意图指向的弹幕 message_id——按
        message_id 等值命中即可）；未提供时回退 ``outcome.target``（弹幕
        ``message_id`` 或文本片段）匹配：message_id 等值 → text 包含/相等。
        全未命中时保守兜底为 batch 最后一条消息的 user_id（"回复最后那条"
        通常是意图所指）；batch 为空或 target 为空时返回 None。该方法只做
        反查，不写状态、不发事件；异常吞掉记 warning 不上抛（决策循环必须继续）。
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
            self._logger.warning(f"反查 reply target user 异常: {exc}")
            return None

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
