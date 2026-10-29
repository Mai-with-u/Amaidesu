"""表情配方与调度：配方覆盖词表、时间曲线、同族延长、最短停留排队、强情绪打断"""

from __future__ import annotations

import pytest

from src.modules.avatar.tracking.channels import BROW, CHANNELS, EYE_OPEN, MOUTH_SMILE
from src.modules.avatar.tracking.expression import FAMILY_TIMING, NEUTRAL, RECIPES, ExpressionScheduler
from src.modules.types.emotion_vocab import Emotion


def _scheduler() -> ExpressionScheduler:
    return ExpressionScheduler(min_dwell_ms=1500, intensity_floor=0.15, intensity_gain=0.65)


def _smile(scheduler: ExpressionScheduler, now_ms: int) -> float:
    return scheduler.sample(now_ms)[0].get(MOUTH_SMILE, 0.0)


def test_recipes_cover_every_emotion_with_known_channels_and_families() -> None:
    assert set(RECIPES) | {NEUTRAL} == {e.value for e in Emotion}
    allowed = set(CHANNELS) | {EYE_OPEN, BROW}
    for recipe in RECIPES.values():
        assert recipe.family in FAMILY_TIMING
        assert set(recipe.offsets) <= allowed


def test_expression_rises_holds_and_falls_back_after_release() -> None:
    scheduler = _scheduler()
    assert scheduler.push("happy", 1.0, 0, key="u1") == "started"
    full = 0.8 * 0.8  # 配方嘴角 0.8 × 压缩后强度 0.8
    assert _smile(scheduler, 0) == pytest.approx(0.0)
    assert 0 < _smile(scheduler, 150) < full  # 0.3 秒浮现中
    assert _smile(scheduler, 300) == pytest.approx(full)
    assert _smile(scheduler, 5000) == pytest.approx(full)  # 没收到收尾就一直保持

    scheduler.release("u1", 6000)
    assert _smile(scheduler, 6000) == pytest.approx(full)
    assert 0 < _smile(scheduler, 6750) < full  # 1.5 秒缓慢回落
    assert _smile(scheduler, 7500) == pytest.approx(0.0)
    assert scheduler.current_emotion(7500) == NEUTRAL


def test_same_family_similar_intensity_only_extends_current_expression() -> None:
    scheduler = _scheduler()
    scheduler.push("happy", 0.6, 0, key="u1")
    assert scheduler.push("excited", 0.7, 400, key="u2") == "extended"
    assert scheduler.current_emotion(500) == "happy"  # 脸不换
    scheduler.release("u1", 800)
    assert scheduler.current_emotion(900) == "happy"  # 归属已移交给 u2，u1 收尾不影响
    scheduler.release("u2", 1000)
    assert scheduler.current_emotion(1100) == NEUTRAL


def test_different_family_waits_for_min_dwell_then_crossfades() -> None:
    scheduler = _scheduler()
    scheduler.push("happy", 0.8, 0, key="u1")
    assert scheduler.push("sad", 0.8, 500, key="u2") == "deferred"
    assert scheduler.current_emotion(1400) == "happy"
    scheduler.sample(1500)  # 停留够 1.5 秒：排队的换上
    assert scheduler.current_emotion(1500) == "sad"
    assert _smile(scheduler, 1600) > _smile(scheduler, 1900)  # 旧的笑在 0.4 秒内让位，难过的嘴角在下压


def test_deferred_expression_is_dropped_when_its_sentence_ended_first() -> None:
    scheduler = _scheduler()
    scheduler.push("happy", 0.8, 0, key="u1")
    scheduler.push("sad", 0.8, 500, key="u2")
    scheduler.release("u2", 1200)  # 排队期间那句已经播完
    scheduler.sample(1500)
    assert scheduler.current_emotion(1500) == "happy"


@pytest.mark.parametrize(("intensity", "outcome"), [(0.8, "started"), (0.4, "deferred")])
def test_strong_startle_breaks_through_min_dwell(intensity: float, outcome: str) -> None:
    scheduler = _scheduler()
    scheduler.push("happy", 0.8, 0, key="u1")
    assert scheduler.push("surprised", intensity, 300, key="u2") == outcome


def test_neutral_releases_current_and_unknown_is_rejected() -> None:
    scheduler = _scheduler()
    scheduler.push("angry", 0.8, 0, key="u1")
    assert scheduler.push("这不是情绪", 0.8, 100, key="u2") == "unknown"
    assert scheduler.push(NEUTRAL, 0.5, 2000, key="u3") == "released"
    assert scheduler.current_emotion(2000) == NEUTRAL


def test_intensity_is_compressed_and_low_energy_emotions_damp_head_motion() -> None:
    scheduler = _scheduler()
    assert scheduler.compress(0.0) == pytest.approx(0.15) and scheduler.compress(1.0) == pytest.approx(0.8)
    scheduler.push("speechless", 1.0, 0, key="u1")
    _, motion = scheduler.sample(1000)
    assert motion < 0.6  # 无语时动作几乎停住
