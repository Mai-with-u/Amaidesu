"""虚拟形象适配器的事件订阅绑定（被动响应业务事件的一侧）

还没移植到面捕合成器的平台（Warudo、VRChat）用的事件订阅入口；
已移植的平台（VTS）由面捕合成器统一订阅这些事件。函数级共享（纯逻辑，不引入基类）：

- ``bind_speech_emotion``：订阅 ``streamer.speech``，把发言携带的
  情绪事实（17 枚举值 + 强度）反射为 ``provider.set_expression`` 调用。
  每具身体各自对发言事实作反射，天然支持多形象（非广播路由）。
  标了 ``emotion_at_playback`` 的句子（一次反应拆成几句时排在后面的）
  先记下，等 ``tts.utterance.started`` 轮到它播放时再切，表情才跟得上声音。
- ``bind_speaking_state``：订阅 ``tts.utterance.started/finished``，
  维护"是否在说话"状态（计数器，started++ / finished--），回调由
  各适配器自接（如 Warudo 说话时点头）。

情绪路径与 TTS 启用正交（``streamer.speech`` 是业务事实，无 TTS 也发布）；
说话状态依赖 TTS 在位——无引擎时 ``tts.utterance.*`` 无发布者，说话反应
不触发，属刻意接受的不对称。装饰性路径 fail-soft：任何异常只记日志，
不影响发布方与决策循环。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional, Tuple

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import ModuleLogger


# 等播放再切表情的句子最多记这么多条：被段落打断丢掉的句子不会再来 started，靠上限淘汰最旧的
_PENDING_EMOTION_LIMIT = 32


async def _reflect_emotion(
    provider: Any, emotion: str, intensity: float, utterance_id: str, logger: ModuleLogger
) -> None:
    """一次 set_expression（装饰性路径：失败与异常只记日志）。"""
    try:
        result = await provider.set_expression(emotion, intensity)
        if not getattr(result, "success", False):
            logger.warning(
                f"情绪反射未成功（已忽略）: emotion={emotion}, "
                f"utterance_id={utterance_id}, err={getattr(result, 'error_message', '')}"
            )
    except Exception:  # noqa: BLE001 - 装饰性路径边界
        logger.exception(f"情绪反射异常（已忽略）: utterance_id={utterance_id}")


def bind_speech_emotion(
    event_bus: Optional[EventBus], provider: Any, logger: ModuleLogger
) -> Optional[Tuple[Any, Any, Any]]:
    """订阅发言与播放事件驱动情绪反射；返回 (发言, 开播, 失败) 三个 handler 供退订（未绑定为 None）。

    虚拟形象是主播的可见身体，情绪是潜意识产物：派发时刻的发言事实（含 text /
    emotion / intensity）即渲染依据，不依赖 TTS 启用与否。例外是标了
    ``emotion_at_playback`` 的句子——它们发出时前一句还在播，先记下，等轮到它
    开始播放再切；播放失败或被丢弃就不切。
    """
    if event_bus is None:
        return None

    pending: OrderedDict[str, Tuple[str, float]] = OrderedDict()

    async def on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        emotion, intensity = str(payload.emotion), float(payload.emotion_intensity)
        if payload.emotion_at_playback:
            pending[payload.utterance_id] = (emotion, intensity)
            while len(pending) > _PENDING_EMOTION_LIMIT:
                pending.popitem(last=False)
            return
        await _reflect_emotion(provider, emotion, intensity, payload.utterance_id, logger)

    async def on_started(event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        entry = pending.pop(payload.utterance_id, None)
        if entry is not None:
            await _reflect_emotion(provider, entry[0], entry[1], payload.utterance_id, logger)

    async def on_failed(event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        pending.pop(payload.utterance_id, None)

    event_bus.on(CoreEvents.STREAMER_SPEECH, on_speech, model_class=StreamerSpeechPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, on_started, model_class=UtteranceStartedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, on_failed, model_class=UtteranceFailedPayload)
    logger.debug("已订阅 streamer.speech / tts.utterance.started|failed → set_expression（自动情绪路径）")
    return (on_speech, on_started, on_failed)


def bind_speaking_state(
    event_bus: Optional[EventBus],
    logger: ModuleLogger,
    on_change: Any,
) -> Optional[Any]:
    """订阅 ``tts.utterance.*`` 维护说话计数；变化时回调 ``on_change(bool)``。

    计数器防错位（started 无配对 finished 时不会永久卡在"说话中"的反面：
    未 started 前计数为 0）。返回 (started_handler, finished_handler) 供退订；
    未绑定时返回 None。
    """
    if event_bus is None:
        return None

    counter = {"speaking": 0}

    async def on_started(event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        counter["speaking"] += 1
        try:
            on_change(True)
        except Exception:  # noqa: BLE001 - 装饰性路径边界
            logger.exception("说话状态回调异常（已忽略）")

    async def on_finished(event_name: str, payload: UtteranceFinishedPayload, source: str) -> None:
        counter["speaking"] = max(0, counter["speaking"] - 1)
        if counter["speaking"] == 0:
            try:
                on_change(False)
            except Exception:  # noqa: BLE001 - 装饰性路径边界
                logger.exception("说话状态回调异常（已忽略）")

    event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, on_started, model_class=UtteranceStartedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, on_finished, model_class=UtteranceFinishedPayload)
    logger.debug("已订阅 tts.utterance.started/finished → 说话状态")
    return (on_started, on_finished)


class AvatarBindings:
    """虚拟形象适配器事件订阅挂载项的全生命周期句柄。

    setup 时逐项 attach（情绪反射 / 说话状态 / 口型渲染器），cleanup 时
    ``close()`` 一次全量退订与摘除——各适配器不再各写一份逐字重复的
    订阅/退订 try/except 样板。全部方法幂等，单项失败记日志不阻断清理
    （装饰性路径的 fail-soft 口径与绑定时一致）。
    """

    def __init__(self, event_bus: Optional[EventBus], logger: ModuleLogger) -> None:
        self._event_bus = event_bus
        self._logger = logger
        self._speech_emotion_handlers: Optional[Tuple[Any, Any, Any]] = None
        self._speaking_state_handles: Optional[Any] = None
        self._lipsync_analyzer: Optional[Any] = None
        self._lip_renderer: Optional[Any] = None

    def attach_emotion_reflection(self, provider: Any) -> None:
        """订阅 ``streamer.speech``（及播放事件），把发言情绪反射为 ``provider.set_expression``。"""
        self._speech_emotion_handlers = bind_speech_emotion(self._event_bus, provider, self._logger)

    def attach_speaking_state(self, on_change: Any) -> None:
        """订阅 ``tts.utterance.*`` 维护说话计数，变化时回调 ``on_change(bool)``。"""
        self._speaking_state_handles = bind_speaking_state(self._event_bus, self._logger, on_change)

    def attach_lip_renderer(self, analyzer: Any, renderer: Any) -> None:
        """把口型渲染器挂到共享口型分析器；``close()`` 时摘除。"""
        self._lipsync_analyzer = analyzer
        self._lip_renderer = renderer
        analyzer.add_renderer(renderer)

    def close(self) -> None:
        """全量退订与摘除（幂等）：事件订阅逐项退订，渲染器从分析器摘除。"""
        if self._event_bus is not None:
            if self._speech_emotion_handlers is not None:
                speech_handler, started_handler, failed_handler = self._speech_emotion_handlers
                try:
                    self._event_bus.off(CoreEvents.STREAMER_SPEECH, speech_handler)
                    self._event_bus.off(CoreEvents.TTS_UTTERANCE_STARTED, started_handler)
                    self._event_bus.off(CoreEvents.TTS_UTTERANCE_FAILED, failed_handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                    self._logger.debug(f"情绪反射退订失败（已忽略）: {exc}")
                self._speech_emotion_handlers = None
            if self._speaking_state_handles is not None:
                started_handler, finished_handler = self._speaking_state_handles
                try:
                    self._event_bus.off(CoreEvents.TTS_UTTERANCE_STARTED, started_handler)
                    self._event_bus.off(CoreEvents.TTS_UTTERANCE_FINISHED, finished_handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                    self._logger.debug(f"tts.utterance.* 退订失败（已忽略）: {exc}")
                self._speaking_state_handles = None
        if self._lipsync_analyzer is not None and self._lip_renderer is not None:
            try:
                self._lipsync_analyzer.remove_renderer(self._lip_renderer)
            except Exception as exc:  # noqa: BLE001 - 摘除失败不阻断清理
                self._logger.debug(f"口型渲染器摘除失败（已忽略）: {exc}")
        self._lip_renderer = None
        self._lipsync_analyzer = None


__all__ = ["AvatarBindings", "bind_speech_emotion", "bind_speaking_state"]
