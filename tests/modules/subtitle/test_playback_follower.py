"""字幕播放事件跟随器测试（字幕源 = TTS 播放事件模式）

覆盖（假总线：publish → 断言 SubtitleService 副作用）：
- ``tts.utterance.started`` → show(该句文本)；
- ``tts.utterance.failed`` → 照常 show(该句文本)（无声音也要给文本）；
- ``tts.utterance.finished`` → clear()；
- event_bus / service 缺任一 → 不绑定。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceFailedPayload, UtteranceFinishedPayload, UtteranceStartedPayload
from src.modules.subtitle.playback_follower import bind_playback_subtitle


def _service():
    svc = AsyncMock()
    svc.show = AsyncMock()
    svc.clear = AsyncMock()
    return svc


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_started_shows_text():
    bus, svc = EventBus(), _service()
    handlers = bind_playback_subtitle(bus, svc, MagicMockLogger())
    assert handlers is not None

    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload(
        utterance_id="utt_p1", speech_text="这句正在播", engine="edge", duration_ms=100))

    svc.show.assert_awaited_once_with("这句正在播", "utt_p1")


@pytest.mark.asyncio
async def test_failed_still_shows_text():
    """合成失败该句照常显示（逐句降级，无声音也要给文本）。"""
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, MagicMockLogger())

    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, UtteranceFailedPayload(
        utterance_id="utt_p2", speech_text="这句失败了但字幕要显示", engine="edge", error_message="boom"))

    svc.show.assert_awaited_once_with("这句失败了但字幕要显示", "utt_p2")


@pytest.mark.asyncio
async def test_finished_clears():
    bus, svc = EventBus(), _service()
    bind_playback_subtitle(bus, svc, MagicMockLogger())

    await _emit(bus, CoreEvents.TTS_UTTERANCE_FINISHED, UtteranceFinishedPayload(
        utterance_id="utt_p3", engine="edge", duration_ms=100))

    svc.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_exception_is_fail_soft():
    """字幕服务异常不影响总线分发（装饰性路径）。"""
    bus, svc = EventBus(), _service()
    svc.show = AsyncMock(side_effect=RuntimeError("boom"))
    bind_playback_subtitle(bus, svc, MagicMockLogger())

    seen = []

    async def _follower(event_name, payload, source=None):
        seen.append(payload.utterance_id)

    bus.on(CoreEvents.TTS_UTTERANCE_STARTED, _follower, model_class=UtteranceStartedPayload)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload(
        utterance_id="utt_p4", speech_text="x", engine="edge", duration_ms=1))
    assert seen == ["utt_p4"]


def test_bind_without_bus_or_service_is_noop():
    assert bind_playback_subtitle(None, _service(), MagicMockLogger()) is None
    assert bind_playback_subtitle(EventBus(), None, MagicMockLogger()) is None


class MagicMockLogger:
    """最小 logger 桩（仅 debug/warning 方法）"""

    def debug(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...
