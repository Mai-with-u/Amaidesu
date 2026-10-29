"""面捕合成器的通道工具与反射类行为：规范词表、钳制、限速、眨眼、眼神、头部漫步"""

from __future__ import annotations

import random

import pytest

from src.modules.avatar.tracking.behaviors import BlinkGenerator, GazeGenerator, HeadWander
from src.modules.avatar.tracking.channels import (
    ARKIT_BLENDSHAPES,
    BILATERAL,
    CHANNELS,
    HEAD_YAW,
    clamp_frame,
    expand,
    eye_look_from_gaze,
    limit_slew,
    rest_frame,
    smile,
)


def test_vocabulary_is_arkit_and_shorthand_expands_both_sides() -> None:
    assert len(set(ARKIT_BLENDSHAPES)) == 52  # 苹果 ARKit 的 52 个表情系数，一个不多一个不少
    assert expand({"mouthSmile": 0.5, "jawOpen": 0.2}) == {
        "mouthSmileLeft": 0.5,
        "mouthSmileRight": 0.5,
        "jawOpen": 0.2,
    }
    assert "mouth" not in BILATERAL and "jaw" not in BILATERAL  # 嘴、下巴的左右平移是方向，不是两侧
    looks = eye_look_from_gaze(0.6, -0.4)  # 看向画面右下 = 角色自己的左下
    assert looks["eyeLookOutLeft"] == looks["eyeLookInRight"] == 0.6 and looks["eyeLookDownLeft"] == 0.4
    assert looks["eyeLookInLeft"] == looks["eyeLookUpRight"] == 0.0
    assert smile({"mouthSmileLeft": 0.8, "mouthFrownRight": 0.4}) == pytest.approx(0.2)


def test_rest_frame_covers_every_channel_and_clamps_rest_smile() -> None:
    frame = rest_frame(rest_smile=3.0)
    assert set(frame) == set(CHANNELS)
    assert frame["eyeBlinkLeft"] == 0.0 and frame["mouthSmileLeft"] == 1.0  # 人设基线超界也被钳回范围
    assert rest_frame(rest_smile=-0.2)["mouthFrownRight"] == pytest.approx(0.2)  # 负数是撇嘴


def test_clamp_keeps_unknown_channels_and_limit_slew_caps_per_frame_change() -> None:
    assert clamp_frame({HEAD_YAW: 99.0, "unknown": 5.0}) == {HEAD_YAW: 30.0, "unknown": 5.0}

    stepped = limit_slew({HEAD_YAW: 0.0, "mouthSmileLeft": 0.0}, {HEAD_YAW: 30.0, "mouthSmileLeft": 1.0}, dt_ms=100)
    assert stepped == {HEAD_YAW: pytest.approx(9.0), "mouthSmileLeft": pytest.approx(0.3)}  # 头 90°/s、嘴角 3/s
    stalled = limit_slew({HEAD_YAW: 0.0}, {HEAD_YAW: 30.0}, dt_ms=5000)
    assert stalled[HEAD_YAW] == pytest.approx(18.0)  # 卡顿 5 秒也只按 200 毫秒走，不瞬移


def _blink_trace(per_min: float, seconds: int, seed: int = 3) -> list[float]:
    blink = BlinkGenerator(random.Random(seed))
    return [blink.closure(t, per_min=per_min) for t in range(0, seconds * 1000, 10)]


def _blink_starts(trace: list[float]) -> list[int]:
    return [i * 10 for i in range(1, len(trace)) if trace[i] > 0 and trace[i - 1] == 0]


def test_blink_closes_fast_opens_slow_and_respects_refractory() -> None:
    trace = _blink_trace(15.0, seconds=600)
    starts = _blink_starts(trace)
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    # 连眨两下（间隔约 0.15 秒 + 一次眨眼时长）之外，两次眨眼至少隔不应期
    assert all(g >= BlinkGenerator.REFRACTORY_MS or g <= 500 for g in gaps)
    assert 12 <= len(starts) / 10 <= 18  # 平均约 15 次/分

    first = starts[0] // 10
    shape = trace[first : first + 30]
    peak = shape.index(1.0)
    assert peak * 10 <= BlinkGenerator.CLOSE_MS  # 80 毫秒内闭满
    opening = [v for v in shape[peak:] if 0 < v < 1.0]
    assert len(opening) * 10 >= BlinkGenerator.OPEN_MS - 20  # 睁开比闭上慢


def test_blink_rate_follows_the_state_rate() -> None:
    reading, idle, speaking = (len(_blink_starts(_blink_trace(rate, seconds=600))) for rate in (6.0, 15.0, 24.0))
    assert reading * 1.8 < idle < speaking / 1.3  # 读弹幕时少眨、说话时多眨


@pytest.mark.parametrize(("chance", "blinks_now"), [(1.0, True), (0.0, False)])
def test_gaze_jump_nudge_blinks_immediately_by_chance(chance: float, blinks_now: bool) -> None:
    blink = BlinkGenerator(random.Random(1))
    assert blink.closure(0, per_min=1.0) == 0.0  # 首次取值排定一个很远的下次眨眼
    blink.nudge(5000, chance)
    assert (blink.closure(5040, per_min=1.0) > 0) is blinks_now


def test_gaze_stays_near_center_with_occasional_glances() -> None:
    gaze = GazeGenerator(random.Random(5))
    samples = [gaze.sample(t) for t in range(0, 120_000, 33)]
    assert all(abs(x) <= 0.6 and abs(y) <= 0.4 for x, y, _ in samples)
    far = [s for s in samples if abs(s[0]) > 0.25 or abs(s[1]) > 0.18]
    assert 0 < len(far) < len(samples) * 0.2  # 瞥视存在，但大部分时间看着中心
    assert any(jumped for _, _, jumped in samples)


def test_fixating_elsewhere_has_no_glances_and_the_move_counts_as_a_jump() -> None:
    gaze = GazeGenerator(random.Random(5))
    gaze.sample(0)
    gaze.set_target(GazeGenerator.FIXATE, (0.4, 0.3), 100)
    samples = [gaze.sample(t) for t in range(133, 60_000, 33)]
    assert samples[0][2] is True  # 从镜头挪到别处：算一次大幅跳动（合成器据此顺带眨眼）
    assert all(abs(x - 0.4) < 0.25 and abs(y - 0.3) < 0.2 for x, y, _ in samples)  # 盯着别处时不再瞥视


def test_reading_steps_along_lines_near_the_chat_area() -> None:
    gaze = GazeGenerator(random.Random(2))
    gaze.set_target(GazeGenerator.READING, (0.45, -0.35), 0)
    xs = [gaze.sample(t)[0] for t in range(0, 6000, 33)]
    assert all(0.3 <= x <= 0.75 for x in xs)  # 在弹幕区附近
    rightward = sum(1 for a, b in zip(xs, xs[1:], strict=False) if b > a + 0.03)
    line_returns = sum(1 for a, b in zip(xs, xs[1:], strict=False) if b < a - 0.1)
    assert rightward >= 8 and line_returns >= 2  # 一行行往右读，读完回到行首


def test_head_wander_is_bounded_and_reproducible_by_seed() -> None:
    def run(seed: int) -> list[tuple[float, float, float]]:
        wander = HeadWander(random.Random(seed))
        return [wander.sample(t, amplitude_deg=3.0) for t in range(0, 60_000, 33)]

    first = run(9)
    assert first == run(9)
    assert all(abs(y) <= 3.0 and abs(p) <= 1.8 and abs(r) <= 2.4 for y, p, r in first)
    assert max(abs(y) for y, _, _ in first) > 1.0  # 真的在动
