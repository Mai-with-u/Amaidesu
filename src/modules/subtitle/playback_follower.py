"""字幕播放事件跟随器（字幕源 = TTS 播放事件）

字幕"一源 → 三面"的播放对齐模式：TTS 引擎在位时，字幕跟随
``tts.utterance.*`` 生命周期——``started`` 显示该句文本、``failed``
照常显示该句文本（无声音也要给文本，原文由 ``speech_text`` 必选字段
承载）、``finished`` 清空。与实际播放对齐：队列丢最旧的台词自然不显示。

择源在装配期完成（有 TTS 引擎 → 本跟随器；无 → 编排层派发直出），
两种模式互斥，非两套源同时跑。
"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import ModuleLogger


def bind_playback_subtitle(
    event_bus: Optional[EventBus],
    subtitle_service: Any,
    logger: ModuleLogger,
) -> Optional[Any]:
    """订阅 ``tts.utterance.*`` 驱动字幕显示/清空；返回 handler 组供退订。

    handler 内部对 ``SubtitleService.show`` / ``clear`` 做异步扇出（字幕
    是装饰性路径，异常吞掉记日志，不影响 TTS 生命周期广播的其他消费者）。
    """
    if event_bus is None or subtitle_service is None:
        return None

    async def _show(text: str, utterance_id: str) -> None:
        try:
            await subtitle_service.show(text, utterance_id)
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            logger.warning(f"字幕 show 异常（已忽略）: utterance_id={utterance_id}, err={exc}")

    async def _clear() -> None:
        try:
            await subtitle_service.clear()
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            logger.warning(f"字幕 clear 异常（已忽略）: err={exc}")

    async def on_started(event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        await _show(payload.speech_text, payload.utterance_id)

    async def on_failed(event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        # 运行期逐句降级：合成失败该句照常显示文本（无声音也要给文本）
        await _show(payload.speech_text, payload.utterance_id)

    async def on_finished(event_name: str, payload: UtteranceFinishedPayload, source: str) -> None:
        await _clear()

    event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, on_started, model_class=UtteranceStartedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, on_failed, model_class=UtteranceFailedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, on_finished, model_class=UtteranceFinishedPayload)
    logger.debug("已订阅 tts.utterance.* → 字幕跟随播放（started/failed 显示，finished 清空）")
    return (on_started, on_failed, on_finished)


def unbind_playback_subtitle(
    event_bus: Optional[EventBus],
    handlers: Any,
    logger: Optional[ModuleLogger] = None,
) -> None:
    """退订播放字幕跟随器（shutdown 时调用；未绑定静默）。"""
    if event_bus is None or handlers is None:
        return
    on_started, on_failed, on_finished = handlers
    for event_name, handler in (
        (CoreEvents.TTS_UTTERANCE_STARTED, on_started),
        (CoreEvents.TTS_UTTERANCE_FAILED, on_failed),
        (CoreEvents.TTS_UTTERANCE_FINISHED, on_finished),
    ):
        try:
            event_bus.off(event_name, handler)
        except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
            if logger is not None:
                logger.debug(f"tts.utterance.* 字幕退订失败（已忽略）: {exc}")


__all__ = ["bind_playback_subtitle", "unbind_playback_subtitle"]
