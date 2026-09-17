"""决策轮执行器（两阶段决策的执行半）。

调度半（flush 循环 + MessageBuffer + ProactiveTrigger + pending 标志 +
``_flush_lock``）留在 StreamerAgent；本组件只负责"拿到一批就执行一轮"：
生成 round_id → 发射 ``streamer.stage``（planning）→ Planner ReAct 决策
→ 产出送发言管线 → 发射 ``planner.decision`` 与 idle 状态。

依赖全部构造期注入（不持 Agent 引用）；历史/流程单/游戏叙事经 provider
回调取用（读数逻辑的事件近邻留在 Agent）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import (
    PlannerBatchItem,
    PlannerDecisionPayload,
    StreamerStagePayload,
)
from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

from .planner import Planner
from .proactive_trigger import ProactiveTrigger
from .room_state import RoomState
from .speech_dispatcher import SpeechDispatcher
from .stats import StreamerStats
from .thinking_stream import ThinkingStreamContext

__all__ = ["DecisionRoundExecutor"]


#: 批次刷新触发原因（MessageBuffer.should_flush 产出）→ 面板可读文案。
#: 原始码仍是机器可读字段（planner.decision.trigger_reason 与日志），此处只译展示文本。
_TRIGGER_REASON_LABEL: Dict[str, str] = {
    "forced": "付费触发（SC / 礼物 / 上舰）",
    "batch_full": "弹幕攒满一批",
    "window_expired": "聚合窗口到期",
    "idle_compensation": "冷场补足一批",
}

#: 主动发言触发源（ProactiveTrigger.should_trigger 返回值）→ 面板可读文案。
_PROACTIVE_REASON_LABEL: Dict[str, str] = {
    "external": "外部指令唤醒",
    "schedule": "定时主动开麦",
    "cold": "冷场主动开麦",
    "rundown": "流程单推进",
    "game": "游戏 Agent 待定夺",
}

#: 静默原因（Planner outcome.silent_reason）→ 面板可读文案。
_SILENT_REASON_LABEL: Dict[str, str] = {
    "natural": "自然终止",
    "max_steps": "超出步数上限",
    "llm_error": "LLM 调用异常",
    "llm_failed": "LLM 返回失败",
    "prompt_render_failed": "提示词渲染失败",
    "assembler_failed": "上下文组装失败",
    "low_confidence": "置信度不足",
}


def _trigger_reason_text(trigger_reason: str) -> str:
    """触发原因码 → 面板可读文案。

    未知码原样返回：新增触发源时面板先显示原码，据此补齐映射，不静默丢信息。
    """
    if not trigger_reason:
        return ""
    if trigger_reason in _TRIGGER_REASON_LABEL:
        return _TRIGGER_REASON_LABEL[trigger_reason]
    if trigger_reason.startswith("proactive:"):
        reason = trigger_reason[len("proactive:") :]
        if reason.startswith("dashboard"):
            return "控制台手动触发"
        return _PROACTIVE_REASON_LABEL.get(reason, f"主动发言（{reason}）")
    if trigger_reason.startswith("dashboard"):
        return "控制台决策测试"
    return trigger_reason


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
        history_provider: Callable[[], Any],
        rundown_text_provider: Callable[[], Optional[str]],
        game_narrative_provider: Callable[[], str],
        body_narrative_provider: Optional[Callable[[], str]] = None,
        logger=None,
    ) -> None:
        """``history_provider`` 等 provider 返回值形态：

        - history: ``Optional[List]``（None/空 = 无历史，鸭子类型对话轮）
        - rundown_text: ``Optional[str]``（None = 无流程单情境）
        - game_narrative: ``str``（可为空串）
        - body_narrative: ``str``（可为空串；AI 玩家身体侧近况，缺省 = 不注入）
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
        self._history_provider = history_provider
        self._rundown_text_provider = rundown_text_provider
        self._game_narrative_provider = game_narrative_provider
        self._body_narrative_provider = body_narrative_provider
        # 决策轮次自增计数器（round_id 生成用；进程内单调）
        self._round_seq: int = 0

    async def execute(
        self,
        batch: List[RoomMessagePayload],
        *,
        forced: bool,
        trigger_reason: str,
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
                        "confidence",
                        "silent_reason",
                    }
                    | None,
                    "speech": str | None,
                    "emotion": str | None,
                    "utterance_id": str | None,
                    "reply_to_message_id": str | None,  # 回复关联键
                    "silent_reason": str | None,  # low_confidence=低置信度压制
                    "error": str | None,  # planner/reply 失败原因，成功为 None
                    "planner_raw": str,  # Planner LLM 原始输出（截断）
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
            detail=_trigger_reason_text(trigger_reason),
        )
        result = await self._decide_round(
            batch,
            round_id=round_id,
            started_ms=started_ms,
            forced=forced,
            trigger_reason=trigger_reason,
            proactive=proactive,
        )
        await self._emit_planner_decision(result, batch)
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
        trigger_reason: str,
        proactive: bool,
    ) -> Dict[str, Any]:
        """两阶段决策执行体（被 ``execute`` 外壳驱动）。

        决策过程副产品（原始输出/请求 ID/分段耗时）随 ``result`` 带回，
        由外壳统一发射决策事件；本方法不直接发事件。
        """
        result: Dict[str, Any] = {
            "round_id": round_id,
            "trigger_reason": trigger_reason,
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

        # 读历史（live_chat 单一事实源；无显式场次时为空）
        history = await self._history_provider()

        # 拼装流程单上下文
        rundown_text = self._rundown_text_provider()

        # 游戏叙事（三通道·事件：MinecraftAgent 等 emit 的 game.* 摘要）
        game_narrative = self._game_narrative_provider()

        # 身体侧近况（采集器分类后的 game.body.* 摘要；缺省不注入）
        body_narrative = self._body_narrative_provider() if self._body_narrative_provider else ""

        # Planner ReAct 决策（循环内完成查信息与 reply 调用；失败细节经
        # Planner.last_failure 带出，供决策事件区分降级原因）
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
                game_narrative=game_narrative,
                body_narrative=body_narrative,
                thinking=thinking,
                round_id=round_id,
            )
        except Exception as exc:
            self._logger.error(f"Planner 调用异常: {exc}", exc_info=True)
            outcome = None
            result["error"] = f"planner_failed: {exc}"
        result["planner_duration_ms"] = now_ms() - planner_started_ms
        result["planner_raw"] = (getattr(self._planner, "last_raw_content", "") or "")[:2000]
        result["llm_request_id"] = getattr(self._planner, "last_request_id", None)

        if outcome is None:
            self._stats.planner_failures += 1
            self._stats.total_no_action += 1
            detail = getattr(self._planner, "last_failure", None)
            result["error"] = result["error"] or (
                f"planner_failed: {detail}" if detail else "planner_failed: 决策循环异常"
            )
            result["total_duration_ms"] = now_ms() - started_ms
            return result

        result["plan"] = {
            "should_reply": outcome.get("replied", False),
            "target": outcome.get("target"),
            "reply_to": outcome.get("reply_to"),
            "topic_summary": outcome.get("topic_summary", ""),
            "reply_guidance": outcome.get("reply_guidance", ""),
            "confidence": outcome.get("confidence"),
            "silent_reason": outcome.get("silent_reason"),
        }
        result["reply_to_message_id"] = outcome.get("reply_to")
        result["silent_reason"] = outcome.get("silent_reason")
        result["reply_duration_ms"] = int(outcome.get("reply_duration_ms", 0) or 0)

        # 未说话（自然终止/超步/LLM 失败/reply 工具失败）——静默收场
        if not outcome.get("replied"):
            self._stats.total_no_action += 1
            self._stats.replyer_failures += int(outcome.get("reply_failures", 0) or 0)
            if outcome.get("error"):
                self._stats.planner_failures += 1
                result["error"] = f"planner_failed: {outcome['error']}"
            result["total_duration_ms"] = now_ms() - started_ms
            return result

        # reply 已在 Planner ReAct 循环内经 reply 工具完成（Planner 阶段耗时含
        # 表达生成）；此处仅把产出送发言管线（speech → TTS / emotion → VTS）。
        speech_info = self._speech.dispatch(
            outcome.get("reply_payload"),
            target_user_id=self._resolve_reply_target_user(outcome, batch),
            reply_to_message_id=outcome.get("reply_to"),
            round_id=round_id,
        )
        if speech_info is not None:
            result["speech"], result["emotion"], result["utterance_id"] = speech_info

        # 成功：保存上下文 + 记录发言时刻 + 频率限制
        self._stats.total_replies += 1
        self._room_state.record_speech(now_ms())
        if proactive:
            # 决策循环内约定 proactive 原因带 "proactive:" 标记（debug/记账共用），此处取其后正文
            reason = trigger_reason[len("proactive:") :] if trigger_reason else "unknown"
            self._proactive_trigger.record_trigger(reason, now_ms())

        result["total_duration_ms"] = now_ms() - started_ms
        return result

    def _resolve_reply_target_user(
        self,
        outcome: Dict[str, Any],
        batch: List[RoomMessagePayload],
    ) -> Optional[str]:
        """从 batch 反查本次回复的观众 user_id。

        优先消费 ``outcome["reply_to"]``（reply 意图指向的弹幕 message_id——按
        message_id 等值命中即可）；未提供时回退 ``outcome["target"]``（弹幕
        ``message_id`` 或文本片段）匹配：message_id 等值 → text 包含/相等。
        全未命中时保守兜底为 batch 最后一条消息的 user_id（"回复最后那条"
        通常是意图所指）；batch 为空或 target 为空时返回 None。该方法只做
        反查，不写状态、不发事件；异常吞掉记 warning 不上抛（决策循环必须继续）。
        """
        reply_to = outcome.get("reply_to")
        target = reply_to or outcome.get("target")
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

    async def _emit_planner_decision(self, result: Dict[str, Any], batch: List[RoomMessagePayload]) -> None:
        """发布 ``planner.decision`` 决策轮记录事件（决策循环内直接 await，保证先于 idle 状态）。

        从 ``_decide_round`` 的结果视图构造决策事件：触发原因、批次摘要、
        决策结论、回复关联、失败原因、原始输出指针与分段耗时全部入事件，
        观察者与互动分析不再依赖日志。
        """
        if self._event_bus is None:
            return
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
                    text=(str(getattr(msg, "content", "") or ""))[:120],
                )
                for msg in batch or []
            ],
            should_reply=bool((result.get("plan") or {}).get("should_reply", False)),
            target=(
                str((result.get("plan") or {}).get("target")) if (result.get("plan") or {}).get("target") else None
            ),
            topic_summary=str((result.get("plan") or {}).get("topic_summary", "") or ""),
            reply_guidance=str((result.get("plan") or {}).get("reply_guidance", "") or ""),
            confidence=float((result.get("plan") or {}).get("confidence", 0.0) or 0.0),
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
