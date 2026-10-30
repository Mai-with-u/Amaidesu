"""面捕合成器核心：静止姿态、表情缓起、口型归属与元音口型、笑撇相抵、身体跟头、眨眼叠加、头部漫步开关、思考与读弹幕、瞥视、脉冲与韵律点头"""

from __future__ import annotations

import random

import pytest

from src.modules.avatar.tracking.channels import (
    BODY_YAW,
    GAZE_X,
    GAZE_Y,
    HEAD_PITCH,
    HEAD_YAW,
    brow_height,
    eye_openness,
    smile,
)
from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.synthesizer import TrackingSynthesizer

FRAME_MS = 33


def _quiet(**overrides: object) -> TrackingSynthesizer:
    """关掉眨眼、眼神、头部漫步与心情的合成器：只剩表情与口型，输出可精确断言。"""
    settings: dict = {
        "blink_enabled": False,
        "gaze_enabled": False,
        "head_wander_enabled": False,
        "mood_enabled": False,
    }
    config = TrackingConfig(**{**settings, **overrides})
    return TrackingSynthesizer(config, rng=random.Random(0))


def _run(synth: TrackingSynthesizer, start_ms: int, end_ms: int) -> list[dict[str, float]]:
    return [synth.step(t) for t in range(start_ms, end_ms, FRAME_MS)]


def test_first_frame_is_rest_pose() -> None:
    frame = _quiet(rest_smile=0.2).step(0)
    assert eye_openness(frame, "Left") == 1.0 and smile(frame) == pytest.approx(0.2) and frame[HEAD_YAW] == 0.0


def test_expression_eases_in_instead_of_snapping() -> None:
    synth = _quiet(rest_smile=0.0)
    synth.step(0)
    synth.express("happy", 1.0, 0, key="u1")
    smiles = [smile(frame) for frame in _run(synth, FRAME_MS, 1000)]
    assert smiles[0] <= 3.0 * FRAME_MS / 1000 + 1e-9  # 第一帧最多走一帧的速度上限
    assert all(b >= a - 1e-9 for a, b in zip(smiles, smiles[1:], strict=False))  # 单调上升，不抖
    assert smiles[-1] == pytest.approx(0.64)


def test_speaking_mouth_follows_lipsync_and_closes_when_signal_goes_stale() -> None:
    synth = _quiet()
    synth.step(0)
    synth.express("surprised", 1.0, 0, key="u1")
    assert _run(synth, FRAME_MS, 600)[-1]["jawOpen"] > 0.3  # 不说话时，惊讶会张嘴

    synth.set_speaking(True)
    synth.on_mouth(0.1, 600)
    assert synth.step(633)["jawOpen"] == pytest.approx(0.1, abs=0.05)  # 说话时下巴只听口型
    assert _run(synth, 666, 1300)[-1]["jawOpen"] == 0.0  # 口型信号过期：闭嘴，不回到惊讶的张嘴


def test_speech_vowels_drive_visemes_and_lip_shape() -> None:
    synth = _quiet(speech_nods_enabled=False)
    synth.step(0)
    synth.set_speaking(True)
    for t in range(FRAME_MS, 400, FRAME_MS):
        synth.on_mouth(0.5, t, {"U": 1.0})
        frame = synth.step(t)
    assert frame["ou"] == pytest.approx(1.0) and frame["aa"] == 0.0  # 元音给 VRM 口型
    assert frame["mouthFunnel"] > 0.3 and frame["jawOpen"] == pytest.approx(0.5)  # u 撮唇，下巴听口型
    synth.on_mouth_end(400)
    after = _run(synth, 433, 800)[-1]
    assert after["ou"] == 0.0 and after["mouthFunnel"] == 0.0  # 说完口型收回


def test_smile_and_frown_cancel_on_the_same_side() -> None:
    synth = _quiet(rest_smile=0.2)
    synth.step(0)
    synth.express("sad", 1.0, 0, key="u1")
    frame = _run(synth, FRAME_MS, 1500)[-1]
    assert frame["mouthSmileLeft"] == 0.0 and frame["mouthFrownLeft"] > 0.2  # 浅笑被撇嘴抵掉，不两个都挂着


def test_body_follows_head_and_eye_look_follows_gaze() -> None:
    synth = _quiet()
    synth.step(0)
    synth.look_at((0.8, 0.0), 0, duration_ms=3000)
    frame = _run(synth, FRAME_MS, 2500)[-1]
    assert frame[HEAD_YAW] > 4.0 and 0.0 < frame[BODY_YAW] < frame[HEAD_YAW] * 0.5  # 身体跟着转一点
    assert frame["eyeLookOutLeft"] == pytest.approx(0.8) and frame["eyeLookInRight"] == pytest.approx(0.8)


def test_blinks_dip_eyes_and_stack_on_narrowed_eyes() -> None:
    config = TrackingConfig(gaze_enabled=False, head_wander_enabled=False)
    synth = TrackingSynthesizer(config, rng=random.Random(4))
    synth.express("tired", 1.0, 0, key="u1")
    frames = _run(synth, 0, 60_000)
    eyes = [eye_openness(frame, "Left") for frame in frames]
    dips = sum(1 for a, b in zip(eyes, eyes[1:], strict=False) if a >= 0.3 and b < 0.3)
    assert 8 <= dips <= 25  # 空闲约 15 次/分
    assert max(eyes[100:]) < 0.75  # 困倦半闭眼期间，眨眼从半闭的位置往下闭


@pytest.mark.parametrize(("enabled", "moves"), [(True, True), (False, False)])
def test_head_wander_toggle(enabled: bool, moves: bool) -> None:
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False)
    synth = TrackingSynthesizer(config, rng=random.Random(2))
    synth.set_head_wander(enabled)
    yaws = [frame[HEAD_YAW] for frame in _run(synth, 0, 20_000)]
    assert (max(abs(y) for y in yaws) > 0.5) is moves


def test_thinking_reads_chat_first_then_looks_away_and_back_when_speaking() -> None:
    synth = _quiet(chat_gaze_x=0.45, chat_gaze_y=-0.35, speech_nods_enabled=False)
    synth.step(0)
    synth.begin_thinking(0, read_first_ms=2000)
    reading = _run(synth, FRAME_MS, 1900)[-1]
    assert (reading[GAZE_X], reading[GAZE_Y]) == (pytest.approx(0.45), pytest.approx(-0.35))  # 先看着弹幕区读
    assert reading[HEAD_YAW] > 2.0  # 头跟着转过去一点
    away = _run(synth, 2000, 3500)[-1]
    assert abs(away[GAZE_X]) >= 0.3 and away[GAZE_Y] > 0.1  # 读完望向侧上方
    assert brow_height(away, "Left") < -0.1  # 眉心微紧
    synth.set_speaking(True)
    speaking = _run(synth, 3500, 4500)[-1]
    assert abs(speaking[GAZE_X]) < 0.05 and abs(speaking[GAZE_Y]) < 0.05  # 一开口就看回镜头
    synth.set_speaking(False)
    synth.end_thinking(4500)
    assert brow_height(_run(synth, 4500, 6000)[-1], "Left") == pytest.approx(0.0, abs=0.02)  # 想完神情散去


def test_proactive_thinking_skips_reading_and_glances_are_rate_limited() -> None:
    synth = _quiet(chat_gaze_x=0.45, chat_gaze_y=-0.35)
    synth.step(0)
    synth.begin_thinking(0)
    assert _run(synth, FRAME_MS, 600)[-1][GAZE_Y] > 0.1  # 没有弹幕可读：直接望向别处
    synth.end_thinking(600)

    assert synth.glance_at_chat(1000, duration_ms=900, min_gap_ms=5000) is True
    assert _run(synth, 1000, 1500)[-1][GAZE_X] == pytest.approx(0.45)  # 瞥一眼弹幕区
    assert abs(_run(synth, 1500, 2500)[-1][GAZE_X]) < 0.05  # 瞥完回来
    assert synth.glance_at_chat(3000, duration_ms=900, min_gap_ms=5000) is False  # 太密不瞥


def test_pulse_reactions_stack_on_top_of_expressions() -> None:
    synth = _quiet()
    synth.step(0)
    synth.express("happy", 0.8, 0, key="u1")
    synth.pulse("startle", 300)
    peak = max(frame["eyeWideLeft"] for frame in _run(synth, FRAME_MS, 1500))
    assert peak > 0.5  # 受惊的睁大眼叠在开心之上
    assert synth.current_emotion(1500) == "happy"  # 脉冲不进表情调度，不打断当前表情


@pytest.mark.parametrize(("nods", "dips"), [(True, True), (False, False)])
def test_speech_rhythm_nods_follow_lipsync_onsets(nods: bool, dips: bool) -> None:
    synth = TrackingSynthesizer(
        TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False, speech_nods_enabled=nods),
        rng=random.Random(3),
    )
    synth.set_speaking(True)
    pitches = []
    for t in range(0, 8000, FRAME_MS):
        # 每 300 毫秒一个音节，每 2.4 秒停顿一次
        phase = t % 2400
        synth.on_mouth(0.5 if phase < 1950 and (t % 300) < 150 else 0.0, t)
        pitches.append(synth.step(t)[HEAD_PITCH])
    assert (min(pitches) < -0.8) is dips


def test_mood_accumulates_into_the_resting_face_and_fades() -> None:
    synth = _quiet(mood_enabled=True, mood_half_life_ms=60_000)
    synth.step(0)
    rest_smile = smile(synth.step(FRAME_MS))
    for i in range(4):  # 连着四句开心的话，每句说完都回落
        t = 1000 + i * 6000
        synth.express("happy", 0.8, t, key=f"u{i}", release_at_ms=t + 2000)
    _run(synth, 2 * FRAME_MS, 26_000)
    valence, _ = synth.mood(26_000)
    assert valence > 0.3
    assert synth.current_emotion(26_000) == "neutral"
    assert smile(synth.step(26_000 + FRAME_MS)) > rest_smile + 0.04  # 表情已经回落，静止脸还带着笑
    assert synth.mood(26_000 + 120_000)[0] < valence / 3  # 两个半衰期后基本回到基线


def test_style_markers_come_only_from_recipes_and_fade_with_them() -> None:
    synth = _quiet()
    assert synth.step(0)["marker_tears"] == 0.0  # 静止时没有任何漫符
    synth.express("crying", 1.0, 0, key="u1", release_at_ms=2000)
    peak = _run(synth, FRAME_MS, 1500)[-1]
    assert peak["marker_tears"] > 0.5 and peak["marker_blush"] == 0.0
    assert _run(synth, 1500, 6000)[-1]["marker_tears"] == 0.0  # 跟着表情一起落回
