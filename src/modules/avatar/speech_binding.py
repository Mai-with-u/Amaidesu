"""虚拟形象适配器的事件订阅绑定（被动响应业务事件的一侧）

适配器的事件订阅入口，函数级共享（纯逻辑，不引入基类）：

- ``bind_speech_emotion``：订阅 ``streamer.speech``，把发言携带的
  情绪事实（17 枚举值 + 强度）反射为 ``provider.set_expression`` 调用。
  每具身体各自对发言事实作反射，天然支持多形象（非广播路由）。
- ``bind_speaking_state``：订阅 ``tts.utterance.started/finished``，
  维护"是否在说话"状态（计数器，started++ / finished--），回调由
  各适配器自接（VTS 暂停 idle 摇摆、Warudo 点头）。

情绪路径与 TTS 启用正交（``streamer.speech`` 是业务事实，无 TTS 也发布）；
说话状态依赖 TTS 在位——无引擎时 ``tts.utterance.*`` 无发布者，说话反应
不触发，属刻意接受的不对称。装饰性路径 fail-soft：任何异常只记日志，
不影响发布方与决策循环。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import ModuleLogger


async def _on_streamer_speech(
    event_name: str,
    payload: StreamerSpeechPayload,
    source: str,
    *,
    provider: Any,
    logger: ModuleLogger,
) -> None:
    """``streamer.speech`` → 情绪反射（每次发言事实驱动一次 set_expression）。"""
    try:
        result = await provider.set_expression(str(payload.emotion), float(payload.emotion_intensity))
        if not getattr(result, "success", False):
            logger.warning(
                f"情绪反射未成功（已忽略）: emotion={payload.emotion}, "
                f"utterance_id={payload.utterance_id}, err={getattr(result, 'error_message', '')}"
            )
    except Exception:  # noqa: BLE001 - 装饰性路径边界
        logger.exception(f"情绪反射异常（已忽略）: utterance_id={getattr(payload, 'utterance_id', '?')}")


def bind_speech_emotion(event_bus: Optional[EventBus], provider: Any, logger: ModuleLogger) -> Optional[Any]:
    """订阅 ``streamer.speech`` 驱动情绪反射；返回 handler 供退订（未绑定为 None）。

    虚拟形象是主播的可见身体，情绪是潜意识产物：派发时刻的发言事实（含 text /
    emotion / intensity）即渲染依据，不依赖 TTS 启用与否。
    """
    if event_bus is None:
        return None

    async def handler(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        await _on_streamer_speech(event_name, payload, source, provider=provider, logger=logger)

    event_bus.on(CoreEvents.STREAMER_SPEECH, handler, model_class=StreamerSpeechPayload)
    logger.debug("已订阅 streamer.speech → set_expression（自动情绪路径）")
    return handler


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
        self._speech_emotion_handler: Optional[Any] = None
        self._speaking_state_handles: Optional[Any] = None
        self._lipsync_analyzer: Optional[Any] = None
        self._lip_renderer: Optional[Any] = None

    def attach_emotion_reflection(self, provider: Any) -> None:
        """订阅 ``streamer.speech``，把发言情绪反射为 ``provider.set_expression``。"""
        self._speech_emotion_handler = bind_speech_emotion(self._event_bus, provider, self._logger)

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
            if self._speech_emotion_handler is not None:
                try:
                    self._event_bus.off(CoreEvents.STREAMER_SPEECH, self._speech_emotion_handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                    self._logger.debug(f"streamer.speech 退订失败（已忽略）: {exc}")
                self._speech_emotion_handler = None
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
