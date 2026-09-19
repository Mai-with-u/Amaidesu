"""发言管线（speech → TTS / emotion → VTS / 字幕 / 动作工具）。

决策循环消费 reply 结构化结果后的全部下游扇出收在这里：
业务事件 ``streamer.speech``、TTS 编排队列、字幕推送、VTS 表情、
动作类工具调用。TTS 队列生命周期由本组件自持（``start``/``stop``），
失败一律降级不阻断决策循环。

扇出策略：异步扇出不阻塞决策循环；任务强引用持有（``_bg_tasks`` 集合），
``stop()`` 末尾限期 2 秒汇合，防止悬挂任务在进程退出/重启窗口继续调用
ToolRegistry / 业务事件总线。对齐 ``EventBus._background_tasks`` 正典模式。
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.logging import ModuleLogger, get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolInvocation

from .utterance_queue import (
    DEFAULT_MAX_QUEUE,
    DEFAULT_RENDER_TIMEOUT_MS,
    UtteranceQueue,
)

__all__ = ["SpeechDispatcher"]

# emotion → VTS 表情参数映射（与 VTSProvider._emotion_map 形态一致）。
# 独立保留一份是为了让发言管线在不持有 VTSProvider 实例时
# 也能把 emotion 翻译为可调用参数；与 VTSProvider 的真实映射解耦
# 也便于单测直接断言。
_EMOTION_TO_VTS_PARAMS: Dict[str, Dict[str, float]] = {
    "happy": {"MouthSmile": 1.0},
    "surprised": {"EyeOpenLeft": 1.0, "EyeOpenRight": 1.0, "MouthOpen": 0.5},
    "sad": {"MouthSmile": -0.3, "EyeOpenLeft": 0.7, "EyeOpenRight": 0.7},
    "angry": {"EyeOpenLeft": 0.6, "EyeOpenRight": 0.6, "MouthSmile": -0.5},
    "shy": {"MouthSmile": 0.3, "EyeOpenLeft": 0.8, "EyeOpenRight": 0.8},
    "love": {"MouthSmile": 0.8, "EyeOpenLeft": 0.9, "EyeOpenRight": 0.9},
    "excited": {"MouthSmile": 1.0, "EyeOpenLeft": 1.0, "EyeOpenRight": 1.0},
    "confused": {"EyeOpenLeft": 0.7, "EyeOpenRight": 0.7, "MouthOpen": 0.2},
    "scared": {"EyeOpenLeft": 0.5, "EyeOpenRight": 0.5, "MouthOpen": 0.3},
    "neutral": {},
}


class SpeechDispatcher:
    """发言管线编排：业务事件 + TTS 队列 + 字幕 + 表情 + 动作。"""

    def __init__(
        self,
        *,
        event_bus: Optional[EventBus],
        subtitle_service: Optional[Any],
        tool_registry: Optional[Any],
        tts_engine: Optional[Any],
        speech_config: Optional[Dict[str, Any]] = None,
        logger: Optional[ModuleLogger] = None,
    ) -> None:
        """``speech_config`` 形态见 StreamerAgent 构造参数文档
        （enabled / max_queue / render_timeout_ms）；``None`` 或
        ``enabled=False`` 时管线整体关闭。``tool_registry`` /
        ``subtitle_service`` / ``tts_engine`` 均鸭子消费（仅调公开方法）。
        """
        self._logger = logger or get_logger("StreamerAgent.SpeechDispatcher")
        self._event_bus = event_bus
        self._tool_registry = tool_registry
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
        队列 worker 持有的 invoke 会先被取消，剩余扇出（业务事件 / 字幕 / VTS /
        动作）在 2 秒内汇合；超时 WARN 不抛，避免阻塞停止流程。
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

    def dispatch(
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
                形态 ``{speech, emotion, actions}``；emotion 为
                ``{"name": str, "intensity": float}``。
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
        - emotion 非空 → ``_spawn`` 派发 VTS 表情工具（异步扇出不阻塞决策循环）
        - actions 非空 → 逐条 ``_spawn`` 派发工具注册表调用（异步扇出不阻塞）
        """
        if not isinstance(reply_payload, dict):
            self._logger.warning(f"reply structured_content 非 dict，跳过发言管线: {type(reply_payload).__name__}")
            return None

        speech = reply_payload.get("speech", "")
        emotion = reply_payload.get("emotion", "")
        actions = reply_payload.get("actions", [])

        cleaned_speech = speech.strip() if isinstance(speech, str) else ""
        # emotion 新契约为 {name, intensity}；兼容旧字符串形态（防御）
        cleaned_emotion_intensity = 0.5
        if isinstance(emotion, dict):
            cleaned_emotion: Optional[str] = str(emotion.get("name", "") or "").strip() or None
            try:
                cleaned_emotion_intensity = min(1.0, max(0.0, float(emotion.get("intensity", 0.5))))
            except (TypeError, ValueError):
                cleaned_emotion_intensity = 0.5
        elif isinstance(emotion, str):
            cleaned_emotion = emotion.strip() or None
        else:
            cleaned_emotion = None

        # 动作类工具调用（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）
        self._schedule_actions(actions)

        # emotion → VTS 表情调用跟随 TTS 启用门：TTS 关闭（无语音/无声卡场景）
        # 时 avatar 表情随同关闭，避免与管线整体退化语义漂移。
        # 与 speech 派发独立（speech 空但 emotion 非空时仍可触发）。
        if cleaned_emotion and self._tts_enabled and self._utterance_queue is not None:
            self._schedule_vts_emotion(cleaned_emotion, intensity=cleaned_emotion_intensity)

        # speech 非空：先发布业务事件 + 写历史（与 TTS 启用与否正交），
        # TTS 启用时复用同一 utterance_id 入 TTS 队列。
        if cleaned_speech:
            utterance_id = self._next_utterance_id()
            self._emit_streamer_speech(
                utterance_id,
                cleaned_speech,
                cleaned_emotion,
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
    # 下游扇出（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）
    # ==================================================================

    def _emit_streamer_speech(
        self,
        utterance_id: str,
        text: str,
        emotion: Optional[str],
        target_user_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        round_id: str = "",
    ) -> None:
        """发布 ``streamer.speech`` 业务事件（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）。"""
        event_bus = self._event_bus
        if event_bus is None:
            return
        payload = StreamerSpeechPayload(
            utterance_id=utterance_id,
            round_id=round_id or None,
            text=text,
            emotion=emotion,
            target_user_id=target_user_id,
            reply_to_message_id=reply_to_message_id,
        )

        async def _do_emit() -> None:
            try:
                await event_bus.emit(
                    CoreEvents.STREAMER_SPEECH,
                    payload,
                    source="streamer_agent.speech",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"streamer.speech 发布失败（已忽略）: utterance_id={utterance_id}, err={exc}")

        self._spawn(_do_emit(), label=f"streamer.speech emit (utt={utterance_id})")

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

    def _schedule_vts_emotion(self, emotion: str, intensity: float = 0.5) -> None:
        """异步触发 VTS 表情调用（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）。

        VTS 工具契约（vts_set_expression）::

            arguments = {
                "parameters": {param_name: value, ...},  # 表情参数映射
                "weight": float,                         # 混合权重（情绪强度驱动）
            }

        若 emotion 不在已知映射表中，DEBUG 日志提示"无映射"并跳过；
        已知映射但参数为空（如 ``neutral``）也照样发起调用，让 VTS
        工具自身的静默处理逻辑统一接管（不做空表达式的特判短路）。

        Args:
            emotion: 情绪枚举名（映射表键）。
            intensity: Replyer 输出的情绪强度 [0.0, 1.0]，直接映射为表情混合权重。
        """
        vts_params = _EMOTION_TO_VTS_PARAMS.get(emotion)
        if vts_params is None:
            self._logger.debug(f"emotion '{emotion}' 未在已知映射表中，跳过 VTS 调用")
            return

        # 在闭包外捕获 registry 引用：避免 LSP 跨闭包推断失败，
        # 同时确保 _invoke_vts 在工具尚未注入时不会抛 AttributeError。
        registry = self._tool_registry
        if registry is None:
            self._logger.debug(f"emotion '{emotion}' 触发条件不满足（tool_registry 缺失），跳过")
            return

        invocation = ToolInvocation(
            tool_name="vts_set_expression",
            arguments={
                "parameters": dict(vts_params),
                "weight": float(min(1.0, max(0.0, intensity))),
            },
            source="streamer_agent.emotion",
        )

        async def _invoke_vts() -> None:
            try:
                await registry.invoke(invocation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                self._logger.warning(f"VTS 表情调用异常（已忽略）: emotion={emotion}, err={exc}")

        self._spawn(_invoke_vts(), label=f"VTS 表情 (emotion={emotion})")

    def _schedule_actions(self, actions: Any) -> None:
        """异步触发动作类工具调用（异步扇出不阻塞决策循环；任务强引用持有，停止时限期汇合）。

        ``actions`` 契约：``[{name: str, parameters: dict}, ...]``（来自
        Replyer 的 tool_calls 非 reply 部分；LLM 通过标准 function calling
        选择的动作类工具）。

        与 ``_schedule_vts_emotion`` 同模式：
        - 每条动作独立 ``_spawn`` 派发（互不阻塞，强引用持有）
        - 注册表缺失时静默跳过
        - 工具失败只记 WARN（注册表 invoke 本身不抛异常，双保险）
        """
        if not isinstance(actions, list) or not actions:
            return

        registry = self._tool_registry
        if registry is None:
            self._logger.debug(f"actions 触发条件不满足（tool_registry 缺失），跳过 {len(actions)} 条")
            return

        for action in actions:
            if not isinstance(action, dict):
                self._logger.debug(f"action 条目非 dict，跳过: {action!r}")
                continue
            name = str(action.get("name", "") or "").strip()
            if not name:
                self._logger.debug("action 条目缺 name，跳过")
                continue
            arguments = action.get("parameters") or action.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            invocation = ToolInvocation(
                tool_name=name,
                arguments=arguments,
                source="streamer_agent.action",
            )

            async def _invoke_action(inv: ToolInvocation = invocation) -> None:
                try:
                    await registry.invoke(inv)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 异步背景任务边界
                    self._logger.warning(f"动作类工具调用异常（已忽略）: tool={inv.tool_name}, err={exc}")

            self._spawn(_invoke_action(), label=f"动作工具 (tool={name})")
