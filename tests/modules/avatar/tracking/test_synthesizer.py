"""面捕合成器核心：静止姿态、表情缓起、口型归属、眨眼叠加、头部漫步开关"""

from __future__ import annotations

import random

import pytest

from src.modules.avatar.tracking.channels import EYE_OPEN_L, HEAD_YAW, MOUTH_OPEN, MOUTH_SMILE
from src.modules.avatar.tracking.config import TrackingConfig
from src.modules.avatar.tracking.synthesizer import TrackingSynthesizer

FRAME_MS = 33


def _quiet(**overrides: object) -> TrackingSynthesizer:
    """关掉眨眼、眼神、头部漫步的合成器：只剩表情与口型，输出可精确断言。"""
    config = TrackingConfig(blink_enabled=False, gaze_enabled=False, head_wander_enabled=False, **overrides)
    return TrackingSynthesizer(config, rng=random.Random(0))


def _run(synth: TrackingSynthesizer, start_ms: int, end_ms: int) -> list[dict[str, float]]:
    return [synth.step(t) for t in range(start_ms, end_ms, FRAME_MS)]


def test_first_frame_is_rest_pose() -> None:
    frame = _quiet(rest_smile=0.2).step(0)
    assert frame[EYE_OPEN_L] == 1.0 and frame[MOUTH_SMILE] == pytest.approx(0.2) and frame[HEAD_YAW] == 0.0


def test_expression_eases_in_instead_of_snapping() -> None:
    synth = _quiet(rest_smile=0.0)
    synth.step(0)
    synth.express("happy", 1.0, 0, key="u1")
    smiles = [frame[MOUTH_SMILE] for frame in _run(synth, FRAME_MS, 1000)]
    assert smiles[0] <= 3.0 * FRAME_MS / 1000 + 1e-9  # 第一帧最多走一帧的速度上限
    assert all(b >= a - 1e-9 for a, b in zip(smiles, smiles[1:], strict=False))  # 单调上升，不抖
    assert smiles[-1] == pytest.approx(0.64)


def test_speaking_mouth_follows_lipsync_and_closes_when_signal_goes_stale() -> None:
    synth = _quiet()
    synth.step(0)
    synth.express("surprised", 1.0, 0, key="u1")
    assert _run(synth, FRAME_MS, 600)[-1][MOUTH_OPEN] > 0.3  # 不说话时，惊讶会张嘴

    synth.set_speaking(True)
    synth.on_mouth(0.1, 600)
    assert synth.step(633)[MOUTH_OPEN] == pytest.approx(0.1, abs=0.05)  # 说话时下巴只听口型
    assert _run(synth, 666, 1300)[-1][MOUTH_OPEN] == 0.0  # 口型信号过期：闭嘴，不回到惊讶的张嘴


def test_blinks_dip_eyes_and_stack_on_narrowed_eyes() -> None:
    config = TrackingConfig(gaze_enabled=False, head_wander_enabled=False)
    synth = TrackingSynthesizer(config, rng=random.Random(4))
    synth.express("tired", 1.0, 0, key="u1")
    frames = _run(synth, 0, 60_000)
    eyes = [frame[EYE_OPEN_L] for frame in frames]
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
