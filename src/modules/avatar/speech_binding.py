"""皮套适配器的事件订阅绑定（被动半）

被动半的事件入口，函数级共享（纯逻辑，不引入基类）：

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

    皮套是主播的可见身体，情绪是潜意识产物：派发时刻的发言事实（含 text /
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


__all__ = ["bind_speech_emotion", "bind_speaking_state"]
