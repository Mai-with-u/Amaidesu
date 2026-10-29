"""心情：表情推动、半衰期回落、给身体的底色与动作、眨眼倍数"""

from __future__ import annotations

import pytest

from src.modules.avatar.tracking.channels import EYE_OPEN, EYE_WIDE, MOUTH_SMILE
from src.modules.avatar.tracking.expression import RECIPES
from src.modules.avatar.tracking.mood import MOOD_PUSH, Mood


def test_every_emotion_pushes_mood() -> None:
    assert set(MOOD_PUSH) == set(RECIPES)


def test_push_accumulates_clamps_and_halves_per_half_life() -> None:
    mood = Mood(half_life_ms=10_000, gain=1.0)
    for _ in range(5):
        mood.push_emotion("excited", 1.0, 0)
    assert mood.valence == 1.0 and mood.arousal == 1.0  # 封顶 1
    mood.decay_to(10_000)
    assert mood.valence == pytest.approx(0.5) and mood.arousal == pytest.approx(0.5)
    mood.push_emotion("这不是情绪", 1.0, 10_000)
    assert mood.valence == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("emotion", "smile_sign", "motion_above_one"),
    [("happy", 1, True), ("sad", -1, False), ("tired", -1, False)],
)
def test_mood_tints_body_and_motion(emotion: str, smile_sign: int, motion_above_one: bool) -> None:
    mood = Mood(half_life_ms=60_000, gain=1.0)
    mood.push_emotion(emotion, 1.0, 0)
    offsets = mood.offsets()
    assert offsets[MOUTH_SMILE] * smile_sign > 0
    assert (mood.motion_scale() > 1.0) is motion_above_one
    if emotion == "tired":
        assert offsets[EYE_OPEN] < 0 and offsets[EYE_WIDE] == 0.0 and mood.blink_scale() < 1.0  # 累了眼皮沉、少动


def test_pulses_nudge_mood_lightly() -> None:
    mood = Mood(half_life_ms=60_000, gain=0.35)
    mood.push_pulse("brighten", 1.0, 0)
    mood.push_pulse("nod", 1.0, 0)  # 点头这类不带情绪的脉冲不推心情
    assert 0 < mood.valence < 0.1
