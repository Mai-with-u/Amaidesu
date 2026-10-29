"""面捕合成器运行时：表情跟着播放时刻走、播放失败作废、无语音即时上脸、驱动挂载与收尾、口型接线、思考与读弹幕、直播间与游戏反应"""

from __future__ import annotations

import random
from typing import Callable

import pytest

from src.modules.avatar.lipsync import MouthSignal
from src.modules.avatar.tracking.channels import GAZE_X, GAZE_Y, brow_height, eye_openness, smile
from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.runtime import TrackingDriver, TrackingRuntime
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.planner import StreamerStagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from tests.config.helpers import wait_until
from tests.helpers import FakeClock
from tests.modules.avatar.tracking.fakes import FakeLipsyncAnalyzer, RecordingDriver
from tests.modules.storage.helpers import make_room_message

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
    assert eye_openness(ok.frames[-1], "Left") < 0.9

    await runtime.detach(ok)
    assert eye_openness(ok.frames[-1], "Left") == 1.0  # 摘下前发静止姿态，形象不会停在半闭眼上
    offline.is_ready = True
    await runtime.stop()
    assert eye_openness(offline.frames[-1], "Left") == 1.0 and runtime.driver_names == []


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
    assert frame["jawOpen"] == pytest.approx(0.5)
    await runtime.stop()
    assert analyzer.renderers == []


async def _advance(runtime: TrackingRuntime, clock: FakeClock, ms: int) -> dict[str, float]:
    frame: dict[str, float] = {}
    for _ in range(max(1, ms // 33)):
        clock.tick(33)
        frame = await runtime.tick_once()
    return frame


def _stage(stage: str) -> StreamerStagePayload:
    return StreamerStagePayload(stage=stage)


async def test_danmaku_glance_then_thinking_reads_it_and_idle_looks_back(
    event_bus: EventBus, fake_clock: FakeClock
) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    await runtime.tick_once()
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(content="主播好"))
    assert (await _advance(runtime, fake_clock, 400))[GAZE_X] == pytest.approx(0.45)  # 有弹幕来：瞥一眼弹幕区
    assert abs((await _advance(runtime, fake_clock, 1200))[GAZE_X]) < 0.05  # 瞥完回来

    await _emit(event_bus, CoreEvents.STREAMER_STAGE, _stage("planning"))  # 弹幕后不久开始想
    reading = await _advance(runtime, fake_clock, 1500)
    assert (reading[GAZE_X], reading[GAZE_Y]) == (pytest.approx(0.45), pytest.approx(-0.35))  # 先读弹幕
    away = await _advance(runtime, fake_clock, 1500)
    assert away[GAZE_Y] > 0.1  # 读完望向别处想
    await _emit(event_bus, CoreEvents.STREAMER_STAGE, _stage("idle"))
    assert abs((await _advance(runtime, fake_clock, 800))[GAZE_Y]) < 0.05
    await runtime.stop()


async def test_proactive_thinking_looks_away_and_playback_ends_it(event_bus: EventBus, fake_clock: FakeClock) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    await _emit(event_bus, CoreEvents.STREAMER_STAGE, _stage("planning"))  # 没有弹幕：主动开口前的思考
    assert (await _advance(runtime, fake_clock, 600))[GAZE_Y] > 0.1
    started = UtteranceStartedPayload(utterance_id="u1", speech_text="嗯", engine="fake")
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    assert abs((await _advance(runtime, fake_clock, 800))[GAZE_Y]) < 0.05  # 开口即看回镜头
    await runtime.stop()


async def test_gift_glances_and_brightens_even_while_speaking_but_danmaku_does_not(
    event_bus: EventBus, fake_clock: FakeClock
) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    started = UtteranceStartedPayload(utterance_id="u1", speech_text="说着话", engine="fake")
    await _emit(event_bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    rest = await _advance(runtime, fake_clock, 300)
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_DANMAKU, make_room_message(content="hi"))
    assert abs((await _advance(runtime, fake_clock, 300))[GAZE_X]) < 0.05  # 说话时弹幕不让她左顾右盼
    await _emit(event_bus, CoreEvents.ROOM_MESSAGE_GIFT, make_room_message(message_type="gift"))
    frame = await _advance(runtime, fake_clock, 400)
    assert frame[GAZE_X] == pytest.approx(0.45) and smile(frame) > smile(rest) + 0.1  # 看一眼礼物、眼睛一亮
    await runtime.stop()


@pytest.mark.parametrize(
    ("event_type", "read", "direction"),
    [
        ("milestone", smile, 1),
        ("attention_required", lambda f: brow_height(f, "Left"), 1),
        ("error", lambda f: brow_height(f, "Left"), -1),
    ],
)
async def test_game_events_trigger_wordless_reactions(
    event_bus: EventBus, fake_clock: FakeClock, event_type: str, read: Callable[[dict], float], direction: int
) -> None:
    runtime = _runtime(event_bus, fake_clock)
    await runtime.start()
    rest = await runtime.tick_once()
    payload = GamePayload(game="minecraft", event_type=event_type, message="游戏里发生了事")
    await _emit(event_bus, f"game.{event_type}", payload)
    frame = await _advance(runtime, fake_clock, 350)
    assert (read(frame) - read(rest)) * direction > 0.1
    await runtime.stop()
