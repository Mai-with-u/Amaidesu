"""身体动作层：站姿跟着持续的情绪走且比表情稳、想久了摆思考站姿开口即放下、说话时叠动作有间隔、手势限频"""

from __future__ import annotations

from typing import List

import pytest
from pydantic import ValidationError

from src.modules.avatar.tracking.body import GESTURE, OVERLAY, STANCE, BodyCue, BodyDirector
from src.modules.avatar.tracking.config import TrackingConfig

SETTLE, HOLD, RETURN, THINK = 2500, 8000, 6000, 3000


def _director(**overrides: object) -> BodyDirector:
    return BodyDirector(TrackingConfig(**overrides))


def _run(
    director: BodyDirector, start: int, end: int, *, emotion: str, thinking: bool = False, speaking: bool = False
) -> List[BodyCue]:
    """按 100 毫秒一拍推进 [start, end]，返回这段时间攒下的动作提示。"""
    for now in range(start, end + 1, 100):
        director.step(now, emotion=emotion, thinking=thinking, speaking=speaking)
    return director.take_cues()


def test_fleeting_emotion_keeps_stance_but_sustained_one_switches() -> None:
    director = _director()
    assert _run(director, 0, SETTLE - 600, emotion="happy") == []  # 一闪而过的情绪不换
    assert _run(director, SETTLE - 500, SETTLE - 100, emotion="neutral") == []
    cues = _run(director, 10_000, 10_000 + SETTLE, emotion="happy")
    assert cues == [BodyCue(STANCE, "hands_front")] and director.stance == "hands_front"


def test_stance_holds_before_next_change_then_returns_to_standing_after_calm() -> None:
    director = _director()
    _run(director, 0, SETTLE, emotion="happy")
    # 开心站姿刚换上，生气持续够久也要等站姿停够最短时长
    assert _run(director, SETTLE + 100, HOLD, emotion="angry") == []
    assert _run(director, HOLD + 100, SETTLE + HOLD + 100, emotion="angry") == [BodyCue(STANCE, "hands_on_hips")]
    calm_from = 20_000
    assert _run(director, calm_from, calm_from + RETURN - 200, emotion="neutral") == []
    assert _run(director, calm_from + RETURN - 100, calm_from + RETURN, emotion="neutral") == [
        BodyCue(STANCE, "standing")
    ]


def test_long_thinking_takes_thinking_pose_and_speaking_drops_it_at_once() -> None:
    director = _director()
    assert _run(director, 0, THINK - 200, emotion="neutral", thinking=True) == []  # 想得快身体不动
    assert _run(director, THINK - 100, THINK, emotion="neutral", thinking=True) == [BodyCue(STANCE, "thinking")]
    # 开口：不等最短停留，也不等情绪持续，直接换到这句话情绪的站姿
    cues = _run(director, THINK + 100, THINK + 100, emotion="sad", speaking=True)
    assert cues == [BodyCue(STANCE, "hand_on_chest")]
    # 之后按普通规则：刚换上的站姿要停够最短时长
    assert _run(director, THINK + 200, THINK + 4000, emotion="angry", speaking=True) == []


def _speak(director: BodyDirector, phrases: List[int]) -> List[BodyCue]:
    """在给定时刻各开口一次（前面停顿 0.5 秒），返回叠加动作提示。"""
    for start in phrases:
        director.on_mouth(0.0, start - 500)
        director.on_mouth(0.5, start)
    return [cue for cue in director.take_cues() if cue.kind == OVERLAY]


def test_phrase_starts_add_overlays_spaced_out() -> None:
    director = _director()
    director.step(0, emotion="neutral", thinking=False, speaking=True)
    assert _speak(director, [1000, 3000, 6000, 9500]) == [BodyCue(OVERLAY, "sway"), BodyCue(OVERLAY, "sway")]
    # 开心时说话改为仰头笑，仰头笑有自己的间隔
    director.step(10_000, emotion="happy", thinking=False, speaking=True)
    assert _speak(director, [14_000, 18_500]) == [BodyCue(OVERLAY, "laugh"), BodyCue(OVERLAY, "sway")]


def test_syllables_within_a_phrase_do_not_count_as_phrase_starts() -> None:
    director = _director()
    director.on_mouth(0.5, 1000)
    director.on_mouth(0.0, 1100)
    director.on_mouth(0.5, 1200)  # 只停了 0.1 秒：同一短语里的下一个音节
    assert [cue.name for cue in director.take_cues()] == ["sway"]


def test_gestures_are_rate_limited_unless_requested_by_the_streamer() -> None:
    director = _director()
    assert director.gesture("wave", 0) and not director.gesture("wave", 5000)
    assert director.gesture("wave", 5000, force=True)  # 主播点名不受限
    assert director.gesture("wave_both", 5000) and not director.gesture("dance", 5000)
    assert director.take_cues() == [BodyCue(GESTURE, "wave"), BodyCue(GESTURE, "wave"), BodyCue(GESTURE, "wave_both")]


def test_disabled_body_layer_emits_nothing() -> None:
    director = _director(body_enabled=False)
    _run(director, 0, 20_000, emotion="angry")
    director.on_mouth(0.5, 21_000)
    assert not director.gesture("wave", 22_000) and director.take_cues() == []


def test_stance_map_override_and_validation() -> None:
    director = _director(body_stances={"neutral": "hands_behind", "happy": "fist_up"})
    assert _run(director, 0, SETTLE, emotion="happy") == [BodyCue(STANCE, "fist_up")]
    assert director.rest_cue() == BodyCue(STANCE, "hands_behind")
    assert _run(director, SETTLE + 100, 20_000, emotion="angry") == []  # 删掉的情绪不换站姿
    with pytest.raises(ValidationError, match="不在词表里"):
        TrackingConfig(body_stances={"happy": "dance"})
    with pytest.raises(ValidationError, match="不是情绪名"):
        TrackingConfig(body_stances={"hungry": "standing"})
