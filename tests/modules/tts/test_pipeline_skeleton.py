"""编排骨架（common.synthesize_play_and_notify / prepare_speech_text）的行为单测

用假引擎直接钉住骨架本身的契约：事件序列、取消截断、失败事件与上抛、
空音频跳过、无 utterance 上下文时静默播放。四引擎接入后的整体契约见
test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import get_logger
from src.modules.tts.common import prepare_speech_text, synthesize_play_and_notify
from tests.modules.tts.helpers import make_audio_manager, record_lifecycle


class FakeEngine:
    """骨架消费面的最小假引擎：暴露 event_bus / logger / audio_manager / 计数。"""

    PROVIDER_NAME = "fake_engine"

    def __init__(self, bus: Optional[EventBus] = None, audio_manager: Any = None) -> None:
        self.event_bus = bus
        self.logger = get_logger("FakeEngine")
        self.audio_manager = audio_manager
        self.render_count = 0
        self.error_count = 0


async def _speak(
    engine: FakeEngine,
    synth: Any,
    *,
    utterance_id: Optional[str] = "utt_1",
    speech_text: str = "你好",
) -> None:
    await synthesize_play_and_notify(
        engine,
        utterance_id=utterance_id,
        speech_text=speech_text,
        synthesize=synth,
    )


async def test_success_emits_started_then_finished_and_counts_one_render(event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    engine = FakeEngine(event_bus, make_audio_manager())
    await _speak(engine, AsyncMock(return_value=([0] * 24000, 24000)))
    await event_bus.cleanup()

    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED, CoreEvents.TTS_UTTERANCE_FINISHED]
    started, finished = (payload for _, payload in captured)
    assert isinstance(started, UtteranceStartedPayload) and isinstance(finished, UtteranceFinishedPayload)
    assert started.duration_ms == finished.duration_ms == 1000  # 24000 样本 @ 24kHz
    engine.audio_manager.play_audio.assert_awaited_once()
    assert engine.render_count == 1 and engine.error_count == 0


async def test_empty_audio_skips_play_and_events(event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    mgr = make_audio_manager()
    engine = FakeEngine(event_bus, mgr)
    await _speak(engine, AsyncMock(return_value=([], 24000)))
    await event_bus.cleanup()

    assert captured == [] and mgr.play_audio.await_count == 0
    assert engine.render_count == 0 and engine.error_count == 0


async def test_synthesis_failure_emits_failed_and_reraises_without_touching_sound_card(event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    mgr = make_audio_manager()
    engine = FakeEngine(event_bus, mgr)
    with pytest.raises(RuntimeError, match="boom"):
        await _speak(engine, AsyncMock(side_effect=RuntimeError("boom")))
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload)
    assert (failed.utterance_id, failed.engine, failed.speech_text) == ("utt_1", "fake_engine", "你好")
    assert "boom" in failed.error_message
    assert not any(name == CoreEvents.TTS_UTTERANCE_FINISHED for name, _ in captured)
    mgr.stop_audio.assert_not_called()  # 合成阶段失败未触达声卡，无需截断
    assert engine.render_count == 0 and engine.error_count == 1


async def test_cancellation_truncates_submitted_playback_and_reraises(event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    mgr = make_audio_manager()
    mgr.play_audio = AsyncMock(side_effect=asyncio.CancelledError())
    engine = FakeEngine(event_bus, mgr)
    with pytest.raises(asyncio.CancelledError):
        await _speak(engine, AsyncMock(return_value=([0] * 4800, 48000)))
    await event_bus.cleanup()

    mgr.stop_audio.assert_called_once()  # 已提交声卡的播放必须显式截断
    assert not any(name == CoreEvents.TTS_UTTERANCE_FAILED for name, _ in captured)
    assert engine.error_count == 0  # 取消不是失败


async def test_missing_utterance_id_plays_silently_without_events(event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    mgr = make_audio_manager()
    await _speak(FakeEngine(event_bus, mgr), AsyncMock(return_value=([0] * 4800, 48000)), utterance_id=None)
    await _speak(FakeEngine(None, mgr), AsyncMock(return_value=([0] * 4800, 48000)))  # 无总线
    await event_bus.cleanup()

    assert captured == [] and mgr.play_audio.await_count == 2


class _LoggerStub:
    def debug(self, message: str) -> None: ...


@pytest.mark.parametrize(
    ("text", "expected"),
    [("你好～(鼓掌)", "你好，鼓掌，"), ("   ", None), ("", None)],
)
def test_prepare_speech_text_normalizes_and_rejects_blank(text: str, expected: Optional[str]) -> None:
    assert prepare_speech_text(_LoggerStub(), text) == expected
