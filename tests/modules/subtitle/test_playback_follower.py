"""字幕播放事件跟随器与两个后端适配：started/failed 显示该句文本，finished 清空；后端只转发 text 并透传 enabled"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.subtitle import DashboardBackend, SubtitleBackend, TkGuiBackend
from src.modules.subtitle.playback_follower import bind_playback_subtitle


class _Logger:
    def debug(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...


def _service() -> AsyncMock:
    return AsyncMock(show=AsyncMock(), clear=AsyncMock())


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await bus.wait_until_drained()


async def test_started_and_failed_show_the_sentence_and_finished_clears() -> None:
    bus, svc = EventBus(), _service()
    assert bind_playback_subtitle(bus, svc, _Logger()) is not None

    started = UtteranceStartedPayload(utterance_id="utt_p1", speech_text="这句正在播", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    svc.show.assert_awaited_once_with("这句正在播", "utt_p1")

    svc.show.reset_mock()
    failed = UtteranceFailedPayload(
        utterance_id="utt_p2", speech_text="失败但字幕要显示", engine="edge", error_message="boom"
    )
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)  # 合成失败逐句降级：无声音也要给文本
    svc.show.assert_awaited_once_with("失败但字幕要显示", "utt_p2")

    finished = UtteranceFinishedPayload(utterance_id="utt_p3", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FINISHED, finished)
    svc.clear.assert_awaited_once()


async def test_subtitle_service_error_does_not_break_the_bus() -> None:
    bus, svc = EventBus(), _service()
    svc.show = AsyncMock(side_effect=RuntimeError("boom"))
    bind_playback_subtitle(bus, svc, _Logger())
    seen: list[str] = []

    async def follower(event_name, payload, source=None) -> None:
        seen.append(payload.utterance_id)

    bus.on(CoreEvents.TTS_UTTERANCE_STARTED, follower, model_class=UtteranceStartedPayload)
    await _emit(
        bus,
        CoreEvents.TTS_UTTERANCE_STARTED,
        UtteranceStartedPayload(utterance_id="utt_p4", speech_text="x", engine="edge", duration_ms=1),
    )
    assert seen == ["utt_p4"]  # 装饰性路径的异常被隔离


def test_binding_needs_both_bus_and_service() -> None:
    assert bind_playback_subtitle(None, _service(), _Logger()) is None
    assert bind_playback_subtitle(EventBus(), None, _Logger()) is None


@pytest.mark.parametrize("enabled", [True, False])
async def test_backends_forward_text_only_and_expose_enabled(enabled: bool) -> None:
    widget = MagicMock(show_subtitle=AsyncMock(), clear_subtitle=AsyncMock())
    widget.subtitle_config.enabled = enabled
    dashboard = DashboardBackend(widget)
    gui = MagicMock(enabled=enabled)
    tk = TkGuiBackend(gui)

    for backend in (dashboard, tk):
        assert isinstance(backend, SubtitleBackend) and backend.enabled is enabled
        await backend.show("hello", utterance_id="u-42")  # 展示位暂未消费 utterance_id，只转发 text
        await backend.clear()

    widget.show_subtitle.assert_awaited_once_with("hello")
    widget.clear_subtitle.assert_awaited_once_with()
    gui.push_subtitle.assert_called_once_with("hello")
    gui.clear_subtitle.assert_called_once_with()  # 线程安全入队，禁止跨线程直调 Tk（会死锁 GUI 线程）
