"""面捕合成器运行时：表情跟着播放时刻走、播放失败作废、无语音即时上脸、驱动挂载与收尾、口型接线"""

from __future__ import annotations

import random

import pytest

from src.modules.avatar.lipsync import MouthSignal
from src.modules.avatar.tracking.channels import EYE_OPEN_L, MOUTH_OPEN
from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.runtime import TrackingDriver, TrackingRuntime
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from tests.config.helpers import wait_until
from tests.helpers import FakeClock
from tests.modules.avatar.tracking.fakes import FakeLipsyncAnalyzer, RecordingDriver

LINGER_MS = 1500


def _runtime(
    bus: EventBus, clock: FakeClock, *, tts_enabled: bool = True, analyzer: object = None, run_loop: bool = False
) -> TrackingRuntime:
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False)
    return TrackingRuntime(
        config,
        event_bus=bus,
        lipsync_analyzer=analyzer,
        tts_enabled=tts_enabled,
        clock=clock.now_ms,
        rng=random.Random(0),
        run_loop=run_loop,
    )


async def _emit(bus: EventBus, name: str, payload: object) -> None:
    await bus.emit(name, payload, source="test")
    await bus.wait_until_drained()


def _speech(uid: str, emotion: str = "happy") -> StreamerSpeechPayload:
    return StreamerSpeechPayload(utterance_id=uid, text="今天也很开心", emotion=emotion, emotion_intensity=0.8)


async def test_expression_waits_for_playback_and_lingers_after_it_ends(
    event_bus: EventBus, fake_clock: FakeClock
) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("u1"))
    assert runtime.current_emotion() == "neutral"  # 还在合成语音：表情不提前上脸

    fake_clock.tick(1200)
    started = UtteranceStartedPayload(utterance_id="u1", speech_text="今天也很开心", engine="fake", duration_ms=1500)
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    assert runtime.current_emotion() == "happy"

    fake_clock.tick(1500)
    finished = UtteranceFinishedPayload(utterance_id="u1", engine="fake", duration_ms=1500)
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_FINISHED, finished)
    fake_clock.tick(LINGER_MS - 100)
    assert runtime.current_emotion() == "happy"  # 句末再停留一会儿
    fake_clock.tick(200)
    assert runtime.current_emotion() == "neutral"
    await runtime.stop()


async def test_failed_utterance_drops_its_emotion(event_bus: EventBus, fake_clock: FakeClock) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("u1", "angry"))
    failed = UtteranceFailedPayload(utterance_id="u1", speech_text="x", engine="fake", error_message="合成失败")
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)
    started = UtteranceStartedPayload(utterance_id="u1", speech_text="x", engine="fake")
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    assert runtime.current_emotion() == "neutral"
    await runtime.stop()


async def test_without_tts_expression_shows_at_once_and_ends_by_text_length(
    event_bus: EventBus, fake_clock: FakeClock
) -> None:
    runtime = _runtime(event_bus, fake_clock, tts_enabled=False)
    await runtime.start()
    await _emit(event_bus, CoreEvents.STREAMER_SPEECH, _speech("u1"))
    assert runtime.current_emotion() == "happy"
    fake_clock.tick(1500 + LINGER_MS + 100)  # 6 个字按 220 毫秒/字不足 1.5 秒，按 1.5 秒算
    assert runtime.current_emotion() == "neutral"
    await runtime.stop()


async def test_tool_expression_holds_then_releases(event_bus: EventBus, fake_clock: FakeClock) -> None:
    runtime = _runtime(event_bus, fake_clock)
    assert runtime.express("smug", 0.7) == "started"
    fake_clock.tick(5900)
    assert runtime.current_emotion() == "smug"
    fake_clock.tick(200)
    assert runtime.current_emotion() == "neutral"


async def test_frames_go_to_ready_drivers_and_rest_pose_is_sent_on_detach_and_stop(
    event_bus: EventBus, fake_clock: FakeClock
) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    ok, offline, broken = (
        RecordingDriver("ok"),
        RecordingDriver("offline", ready=False),
        RecordingDriver("x", fail=True),
    )
    assert isinstance(ok, TrackingDriver)
    for driver in (ok, offline, broken):
        await runtime.attach(driver)
    runtime.express("tired", 1.0)
    for _ in range(30):
        fake_clock.tick(33)
        await runtime.tick_once()
    assert len(ok.frames) >= 30 and offline.frames == []  # 未就绪的跳过；写失败的不拖累别人
    assert ok.frames[-1][EYE_OPEN_L] < 0.9

    await runtime.detach(ok)
    assert ok.frames[-1][EYE_OPEN_L] == 1.0  # 摘下前发静止姿态，形象不会停在半闭眼上
    offline.is_ready = True
    await runtime.stop()
    assert offline.frames[-1][EYE_OPEN_L] == 1.0 and runtime.driver_names == []


@pytest.mark.parametrize("attach_first", [True, False])
async def test_frame_loop_runs_once_started_with_a_driver_regardless_of_order(
    event_bus: EventBus, fake_clock: FakeClock, attach_first: bool
) -> None:
    runtime = _runtime(event_bus, fake_clock, run_loop=True)
    driver = RecordingDriver()
    if attach_first:
        await runtime.attach(driver)
        await runtime.start()
    else:
        await runtime.start()
        await runtime.attach(driver)
    await wait_until(lambda: len(driver.frames) >= 2, what="帧循环自己推出至少两帧")
    await runtime.stop()


async def test_lipsync_signal_reaches_mouth_while_speaking(event_bus: EventBus, fake_clock: FakeClock) -> None:
    analyzer = FakeLipsyncAnalyzer()
    runtime = _runtime(event_bus, fake_clock, analyzer=analyzer)
    await runtime.start()
    assert len(analyzer.renderers) == 1
    started = UtteranceStartedPayload(utterance_id="u1", speech_text="啊", engine="fake")
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    await runtime.tick_once()
    for _ in range(10):
        fake_clock.tick(33)
        await analyzer.renderers[0].on_mouth_signal(MouthSignal(mouth_open=0.5))
        frame = await runtime.tick_once()
    assert frame[MOUTH_OPEN] == pytest.approx(0.5)
    await runtime.stop()
    assert analyzer.renderers == []
