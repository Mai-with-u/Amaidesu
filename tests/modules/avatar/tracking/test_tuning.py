"""形象调校：配方覆盖、实时改参不打断表情、手动摆姿到期交还、状态快照、驱动校准"""

from __future__ import annotations

import random
from typing import Dict, Mapping

import pytest
from pydantic import ValidationError

from src.modules.avatar.tracking.channels import EYE_OPEN, EYE_OPEN_L, HEAD_YAW, MOUTH_SMILE
from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.expression import POSITIVE, RECIPES, ExpressionScheduler, build_recipes
from src.modules.avatar.tracking.runtime import TrackingRuntime
from src.modules.avatar.tracking.synthesizer import TrackingSynthesizer
from tests.helpers import FakeClock
from tests.modules.avatar.tracking.fakes import RecordingDriver

FRAME_MS = 33
# 按人设把开心改成眯成 ^^ 的笑眼：眼睛几乎闭上、嘴角拉满
SQUINT_HAPPY = {EYE_OPEN: -0.85, MOUTH_SMILE: 1.0, "motion": 1.2}


def _config(**overrides: object) -> TrackingConfig:
    quiet: dict = {"blink_enabled": False, "gaze_enabled": False, "head_wander_enabled": False, "mood_enabled": False}
    return TrackingConfig(**{**quiet, **overrides})


def _settle(synth: TrackingSynthesizer, start_ms: int, end_ms: int) -> Dict[str, float]:
    frame: Dict[str, float] = {}
    for t in range(start_ms, end_ms, FRAME_MS):
        frame = synth.step(t)
    return frame


def test_override_replaces_offsets_but_keeps_family() -> None:
    recipes = build_recipes({"happy": SQUINT_HAPPY})
    assert dict(recipes["happy"].offsets) == {EYE_OPEN: -0.85, MOUTH_SMILE: 1.0}
    assert recipes["happy"].family == POSITIVE and recipes["happy"].motion == 1.2
    assert recipes["sad"] is RECIPES["sad"]  # 没写覆盖的情绪沿用默认


@pytest.mark.parametrize(
    "recipes",
    [{"sleepy": {MOUTH_SMILE: 0.5}}, {"happy": {"nose": 0.5}}, {"happy": {MOUTH_SMILE: 3.0}}, {"happy": {"motion": 9}}],
)
def test_config_rejects_unknown_or_out_of_range_overrides(recipes: dict) -> None:
    with pytest.raises(ValidationError):
        TrackingConfig(recipes=recipes)


def test_new_recipe_applies_to_the_expression_already_on_face() -> None:
    scheduler = ExpressionScheduler(min_dwell_ms=1500, intensity_floor=0.15, intensity_gain=0.65)
    scheduler.push("happy", 1.0, 0, key="u1")
    assert EYE_OPEN in scheduler.sample(1000)[0]
    scheduler.set_recipes(build_recipes({"happy": {MOUTH_SMILE: 0.2}}))
    offsets, _ = scheduler.sample(1033)
    assert EYE_OPEN not in offsets and offsets[MOUTH_SMILE] == pytest.approx(0.2 * 0.8)


def test_reconfigure_moves_rest_pose_and_recipes_without_restart() -> None:
    synth = TrackingSynthesizer(_config(), rng=random.Random(0))
    assert _settle(synth, 0, 1000)[MOUTH_SMILE] == pytest.approx(0.1)
    synth.reconfigure(_config(rest_smile=0.4, recipes={"happy": SQUINT_HAPPY}))
    assert _settle(synth, 1000, 2000)[MOUTH_SMILE] == pytest.approx(0.4)  # 人设基线实时生效
    synth.express("happy", 1.0, 2000, key="u1")
    assert _settle(synth, 2000, 3500)[EYE_OPEN_L] < 0.4  # 新配方：开心时眯眼
    assert synth.recipes()["happy"].offsets[EYE_OPEN] == -0.85


def test_manual_pose_wins_until_it_expires_then_hands_back() -> None:
    synth = TrackingSynthesizer(_config(), rng=random.Random(0))
    synth.set_manual({HEAD_YAW: 20.0, "not_a_channel": 1.0}, until_ms=2000)
    frame = _settle(synth, 0, 1500)
    assert frame[HEAD_YAW] == pytest.approx(20.0) and "not_a_channel" not in frame
    assert synth.manual_active(1500) and not synth.manual_active(2000)
    assert _settle(synth, 2000, 3500)[HEAD_YAW] == pytest.approx(0.0)  # 到期后平滑回到自动


class CalibratedDriver(RecordingDriver):
    """带校准项的驱动替身。"""

    def __init__(self) -> None:
        super().__init__("calibrated")
        self.values = {"eye_open_rest": 0.55}

    def calibration(self) -> Dict[str, float]:
        return dict(self.values)

    def calibrate(self, values: Mapping[str, float]) -> None:
        if set(values) - set(self.values):
            raise ValueError("未知校准项")
        self.values.update(values)


async def test_runtime_snapshot_reconfigure_and_calibration(fake_clock: FakeClock) -> None:
    runtime = TrackingRuntime(_config(), clock=fake_clock.now_ms, rng=random.Random(0), run_loop=False)
    await runtime.start()
    plain, calibrated = RecordingDriver("plain"), CalibratedDriver()
    await runtime.attach(plain)
    await runtime.attach(calibrated)

    runtime.reconfigure(_config(rest_smile=0.3))
    assert runtime.config.rest_smile == 0.3
    runtime.set_manual({HEAD_YAW: 10.0}, hold_ms=5000)
    for _ in range(30):
        fake_clock.tick(FRAME_MS)
        await runtime.tick_once()
    state = runtime.snapshot()
    assert state["frame"][HEAD_YAW] == pytest.approx(10.0) and state["manual"] is True
    assert state["drivers"] == ["plain", "calibrated"] and state["emotion"] == "neutral"
    assert set(state["mood"]) == {"valence", "arousal"} and state["speaking"] is False
    runtime.clear_manual()
    assert runtime.snapshot()["manual"] is False

    assert runtime.calibrations() == {"calibrated": {"eye_open_rest": 0.55}}  # 没有校准项的驱动不列
    assert runtime.calibrate("calibrated", {"eye_open_rest": 0.6}) is True
    assert calibrated.values["eye_open_rest"] == 0.6
    assert runtime.calibrate("plain", {"eye_open_rest": 0.6}) is False
    with pytest.raises(ValueError):
        runtime.calibrate("calibrated", {"nose": 1.0})
    await runtime.stop()
