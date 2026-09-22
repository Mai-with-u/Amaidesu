"""发言管线（speech → TTS / 字幕）。

决策循环消费 reply 结构化结果后的全部下游扇出收在这里：
业务事件 ``streamer.speech``、TTS 编排队列、字幕推送。情绪渲染不在
本管线扇出——皮套适配器订阅 ``streamer.speech`` 自行反射（自动情绪
路径），主播域不携带任何皮套平台知识。TTS 队列生命周期由本组件自持
（``start``/``stop``），失败一律降级不阻断决策循环。

扇出策略：``streamer.speech`` 业务事件在派发路径上同步 ``await`` 发出——
观察端时间线按实际发生顺序渲染，依赖"发言先于轮末决策记录与空闲状态"
的先后契约，不能延后一拍；字幕推送与 TTS 入队仍为异步扇出（任务强引用
持有 ``_bg_tasks`` 集合，``stop()`` 末尾限期 2 秒汇合，防止悬挂任务在
进程退出/重启窗口继续调用业务事件总线），对齐 ``EventBus._background_tasks``
正典模式。
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.logging import ModuleLogger, get_logger
from src.modules.time_utils import now_ms

from .utterance_queue import (
    DEFAULT_MAX_QUEUE,
    DEFAULT_RENDER_TIMEOUT_MS,
    UtteranceQueue,
)

__all__ = ["SpeechDispatcher"]


class SpeechDispatcher:
    """发言管线编排：业务事件 + TTS 队列 + 字幕 + 表情。"""

    def __init__(
        self,
        *,
        event_bus: Optional[EventBus],
        subtitle_service: Optional[Any],
        tts_engine: Optional[Any],
        speech_config: Optional[Dict[str, Any]] = None,
        logger: Optional[ModuleLogger] = None,
    ) -> None:
        """``speech_config`` 形态见 StreamerAgent 构造参数文档
        （enabled / max_queue / render_timeout_ms）；``None`` 或
        ``enabled=False`` 时管线整体关闭。``subtitle_service`` /
        ``tts_engine`` 均鸭子消费（仅调公开方法）。
        """
        self._logger = logger or get_logger("StreamerAgent.SpeechDispatcher")
        self._event_bus = event_bus
        speech_cfg = speech_config or {}
        self._tts_enabled: bool = bool(speech_cfg.get("enabled", False))
        self._speech_max_queue: int = int(speech_cfg.get("max_queue", DEFAULT_MAX_QUEUE))
        self._speech_render_timeout_ms: int = int(speech_cfg.get("render_timeout_ms", DEFAULT_RENDER_TIMEOUT_MS))
        self._tts_engine = tts_engine
        self._subtitle_service = subtitle_service
        self._utterance_queue: Optional[UtteranceQueue] = None
        # utterance_id 自增计数器（进程内单调；启动时复位为 0，首次自增到 1）
        self._utterance_seq: int = 0
        # 异步扇出任务强引用集合（fire-and-forget 不阻塞决策循环；持有以避免
        # 事件循环的弱引用导致任务被 GC；停止时限期汇合防止悬挂）。
        self._bg_tasks: set = set()

    def _spawn(self, coro: Coroutine[Any, Any, Any], *, label: str) -> None:
        """把后台 coroutine 创建并纳入强引用持有（决策循环同步返回，不等待）。

        行为对齐 ``EventBus._background_tasks`` 正典模式：
        - ``RuntimeError``（无事件循环等）→ WARN 返回，不抛
        - 成功则 ``_bg_tasks.add(task)`` + ``add_done_callback`` 自动 ``discard``
        - 非取消导致的未捕获异常 → WARN（异常已吞，不外传）
        """
        try:
            task = asyncio.create_task(coro)
        except RuntimeError as exc:
            self._logger.warning(f"{label} 任务创建失败（已忽略）: err={exc}")
            return

        self._bg_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self._bg_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self._logger.warning(f"{label} 后台任务未捕获异常: err={exc}")

        task.add_done_callback(_on_done)

    # -----只读状态（观察面 / 测试）-----

    @property
    def tts_enabled(self) -> bool:
        """TTS 下游管线当前是否启用（降级后为 False）。"""
        return self._tts_enabled

    @property
    def utterance_queue(self) -> Optional[UtteranceQueue]:
        """编排队列实例（未启动 / 降级时为 None）。"""
        return self._utterance_queue

    @property
    def utterance_seq(self) -> int:
        """utterance_id 计数器当前值（观察面）。"""
        return self._utterance_seq

    # ==================================================================
    # 生命周期（TTS 编排队列自持）
    # ==================================================================

    async def start(self) -> None:
        """启动发言管线：仅在 TTS 显式启用且引擎注入时构造队列；
        否则保持禁用（决策循环行为与管线关闭时一致：只读 result.success，
        不消费 result.content）。启动失败降级为关闭（fail-soft）。
        """
        if not self._tts_enabled:
            return
        if self._tts_engine is None:
            self._logger.warning("TTS 已启用但未注入 tts_engine，发言管线降级为关闭")
            self._tts_enabled = False
            return
        try:
            engine = self._tts_engine

            async def _speak(text: str, utterance_id: Optional[str] = None) -> None:
                """编排队列 → TTS 引擎的 speak 适配器。"""
                await engine.handle_speech(text, utterance_id=utterance_id)

            self._utterance_queue = UtteranceQueue(
                speak=_speak,
                logger_name="StreamerAgent.UtteranceQueue",
                max_queue=self._speech_max_queue,
                render_timeout_ms=self._speech_render_timeout_ms,
            )
            await self._utterance_queue.start()
        except Exception as exc:
            self._logger.warning(f"启动发言管线失败，已降级为关闭: {exc}")
            self._utterance_queue = None
            self._tts_enabled = False

    async def stop(self) -> None:
        """停止发言管线：先停 TTS 队列，再汇合在飞扇出任务；可重复调用。

        顺序：``utterance_queue.stop()`` → ``wait_for(gather(_bg_tasks))``。
        队列 worker 持有的 invoke 会先被取消，剩余扇出（业务事件 / 字幕）
        在 2 秒内汇合；超时 WARN 不抛，避免阻塞停止流程。
        """
        if self._utterance_queue is not None:
            try:
                await self._utterance_queue.stop()
            except Exception as exc:
                self._logger.warning(f"停止发言管线失败: {exc}")
            self._utterance_queue = None

        if self._bg_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._bg_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                self._logger.warning(f"停止时仍有 {len(self._bg_tasks)} 个后台扇出任务未汇合（timeout=2.0s）")
            except Exception as exc:
                self._logger.warning(f"汇合后台扇出任务失败: {exc}")

    # ==================================================================
    # 派发入口
    # ==================================================================

    def _next_utterance_id(self) -> str:
        """生成下一个 utterance_id（格式 ``utt_{epoch_ms}_{seq}``）。

        自增计数器在 ``__init__`` 中初始化为 0，首次调用返回 seq=1。
        seq 是进程内单调递增，保证同场内 utterance_id 唯一。
        """
        self._utterance_seq += 1
        return f"utt_{now_ms()}_{self._utterance_seq}"

    async def dispatch(
        self,
        reply_payload: Any,
        *,
        target_user_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        round_id: str = "",
    ) -> Optional[tuple]:
        """消费 reply 结构化结果并触发下游管线（决策循环安全：永不抛异常）。

        Args:
            reply_payload: ``ToolExecutionResult.structured_content``（dict），
                形态 ``{speech, emotion}``；emotion 为
                ``{"name": str, "intensity": float}``（name 必为合法枚举值、
                intensity 已 clamp，由 replyer 保证）。
            target_user_id: 本次回复的观众 user_id（可选；透传到
                ``streamer.speech`` 业务事件，None 表示主动发言/无特定对象）。
            reply_to_message_id: 本次回复所指向弹幕的 message_id（可选；
                来自 Planner 决策输出，透传到发言事件并落库为互动关联）。
            round_id: 决策轮次 ID（可选；透传到 ``streamer.speech`` 事件供
                观察器把发言卡与该轮思考过程成组）。

        Returns:
            ``(speech, emotion, utterance_id)`` 三元组（speech 为空或输入
            非 dict 时返回 None）。

        行为契约：
        - 非 dict 输入 → WARN 日志 + 直接返回（决策循环不受影响）
        - TTS 未启用 → 仍发布 ``streamer.speech`` 业务事件（存储落库由 StorageLedger 订阅完成）
          （TTS 启用与否与主播发言业务事实正交；下游消费者仅依赖业务事件）
        - speech 非空 → 生成 utterance_id + 发布业务事件 + 写入历史；TTS 启用时
          复用同一 utterance_id 入 TTS 队列（与 ``tts.utterance.*`` 共用关联键）
        - 业务事件同步 await 发出，先于本轮 ``planner.decision`` 与 idle 状态——
          观察端时间线按实际发生顺序渲染依赖该先后关系
        - emotion 随业务事件发布，由皮套适配器订阅反射（本管线不做情绪扇出）
        """
        if not isinstance(reply_payload, dict):
            self._logger.warning(f"reply structured_content 非 dict，跳过发言管线: {type(reply_payload).__name__}")
            return None

        speech = reply_payload.get("speech", "")
        emotion = reply_payload.get("emotion", {})

        cleaned_speech = speech.strip() if isinstance(speech, str) else ""
        # 情绪契约：replyer 保证 emotion 为 {name, intensity} 且 name 已降级为
        # 合法枚举值（缺失→neutral）、intensity 已 clamp——此处规范化后透传，
        # 使 ``streamer.speech`` 事件的 emotion/emotion_intensity 必有值；
        # 键缺失时按生产者同款语义取 neutral / 0.5（dispatch 永不抛异常边界）。
        emotion_obj = emotion if isinstance(emotion, dict) else {}
        cleaned_emotion = str(emotion_obj.get("name") or "").strip() or "neutral"
        try:
            cleaned_emotion_intensity = min(1.0, max(0.0, float(emotion_obj.get("intensity", 0.5))))
        except (TypeError, ValueError):
            cleaned_emotion_intensity = 0.5

        # speech 非空：先发布业务事件 + 写历史（与 TTS 启用与否正交），
        # TTS 启用时复用同一 utterance_id 入 TTS 队列。
        if cleaned_speech:
            utterance_id = self._next_utterance_id()
            await self._emit_streamer_speech(
                utterance_id,
                cleaned_speech,
                cleaned_emotion,
                cleaned_emotion_intensity,
                target_user_id,
                reply_to_message_id=reply_to_message_id,
                round_id=round_id,
            )
            self._schedule_subtitle_show(cleaned_speech, utterance_id)
            if self._tts_enabled and self._utterance_queue is not None:
                self._spawn(
                    self._utterance_queue.enqueue(utterance_id, cleaned_speech),
                    label="utterance 入队",
                )
            return cleaned_speech, cleaned_emotion, utterance_id

        return None

    # ==================================================================
    # 下游扇出（业务事件同步发出；字幕/TTS 异步扇出不阻塞决策循环）
    # ==================================================================

    async def _emit_streamer_speech(
        self,
        utterance_id: str,
        text: str,
        emotion: str,
        emotion_intensity: float,
        target_user_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        round_id: str = "",
    ) -> None:
        """发布 ``streamer.speech`` 业务事件（同步 await 发出，保证先于轮末决策记录与空闲状态；失败不反噬决策循环）。"""
        event_bus = self._event_bus
        if event_bus is None:
            return
        payload = StreamerSpeechPayload(
            utterance_id=utterance_id,
            round_id=round_id or None,
            text=text,
            emotion=emotion,
            emotion_intensity=emotion_intensity,
            target_user_id=target_user_id,
            reply_to_message_id=reply_to_message_id,
        )

        try:
            await event_bus.emit(
                CoreEvents.STREAMER_SPEECH,
                payload,
                source="streamer_agent.speech",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 观测事件不阻断决策循环
            self._logger.warning(f"streamer.speech 发布失败（已忽略）: utterance_id={utterance_id}, err={exc}")

    def _schedule_subtitle_show(self, text: str, utterance_id: str) -> None:
        """异步触发字幕推送（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）。

        调用 ``SubtitleService.show(text, utterance_id)``：服务内部并行
        广播到所有已注册 Backend，单 Backend 故障隔离由服务负责，本方法
        只关心"任务跑出去"。与 ``_emit_streamer_speech`` 同模式——
        决策循环同步路径绝不 await 异步操作。
        """
        service = self._subtitle_service
        if service is None:
            return

        async def _do_show() -> None:
            try:
                await service.show(text, utterance_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"字幕 show 异常（已忽略）: utterance_id={utterance_id}, err={exc}")

        self._spawn(_do_show(), label=f"subtitle show (utt={utterance_id})")
