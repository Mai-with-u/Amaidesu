"""VTS 待机微动：参数写入与归零、说话暂停、head/body 同名合并、额外参数、随机停留、基线表情、保鲜写入、量纲缩放"""

import asyncio
import random
import time

import pytest

from src.modules.avatar.platform.vts.idle_motion_controller import (
    _PARAM_REFRESH_INTERVAL_S,
    AxisWander,
    IdleMotionController,
)
from src.modules.avatar.platform.vts.vts_provider import VTSProvider


def _controller(*, speaking: bool = False, **kwargs) -> tuple[IdleMotionController, list]:
    """带记录回调的控制器；calls 按写入顺序收集 (参数名, 值)。"""
    calls: list = []

    async def set_param(name: str, value: float) -> bool:
        calls.append((name, value))
        return True

    defaults = {"update_interval_ms": 20.0, "fade_speed": 0.5, "head_amplitude": 0.1, "body_amplitude": 0.05}
    controller = IdleMotionController(
        logger_name="test.idle",
        is_connected=lambda: True,
        is_speaking=lambda: speaking,
        set_parameter=set_param,
        **{**defaults, **kwargs},
    )
    return controller, calls


async def _run(controller: IdleMotionController, seconds: float) -> None:
    controller.start()
    await asyncio.sleep(seconds)
    await controller.stop()


async def test_start_sends_all_axes_and_stop_zeroes_them() -> None:
    ctrl, calls = _controller(
        param_head_x="HeadX",
        param_head_y="HeadY",
        param_head_z="HeadZ",
        param_body_x="BodyX",
        param_body_y="BodyY",
        param_body_z="BodyZ",
        speed=1.0,
    )
    await _run(ctrl, 0.08)

    names = {name for name, _ in calls}
    assert names == {"HeadX", "HeadY", "HeadZ", "BodyX", "BodyY", "BodyZ"}
    assert {name for name, value in calls if value == 0.0} == names  # 停止后每个参数都写过零值


@pytest.mark.parametrize(("speech_pause_enabled", "keeps_moving"), [(True, False), (False, True)])
async def test_speech_pause_flag_controls_idle_while_speaking(speech_pause_enabled: bool, keeps_moving: bool) -> None:
    ctrl, calls = _controller(
        speaking=True, head_amplitude=1.0, body_amplitude=1.0, speech_pause_enabled=speech_pause_enabled
    )
    await _run(ctrl, 0.6)
    max_abs = max((abs(value) for _, value in calls), default=0.0)
    # 说话时默认暂停并归零；开关关掉后继续产生待机动作
    assert (max_abs > 0.001) is keeps_moving


async def test_set_parameter_names_redirects_writes_to_new_names() -> None:
    ctrl, calls = _controller(param_head_x="HeadX", head_amplitude=1.0)
    ctrl.set_parameter_names(param_head_x="NewHeadX", param_head_y="NewHeadY")
    await _run(ctrl, 0.06)
    names = {name for name, _ in calls}
    assert {"NewHeadX", "NewHeadY"} <= names and "HeadX" not in names


async def test_shared_head_body_params_are_merged_not_overwritten() -> None:
    axes = {f"param_{part}_{axis}": f"FaceAngle{axis.upper()}" for part in ("head", "body") for axis in "xyz"}
    ctrl, calls = _controller(head_amplitude=3.0, body_amplitude=1.0, rng=random.Random(7), **axes)

    targets = ctrl._compute_targets()
    assert set(targets) == {"FaceAngleX", "FaceAngleY", "FaceAngleZ"}  # 同名参数合并后只出现一次
    assert all(abs(value) <= 3.0 + 1.0 for value in targets.values())  # 合并值不超过两幅度之和

    samples = []
    for k in range(20):
        ctrl._start_time = time.time() - k * 1.3
        samples.append(abs(ctrl._compute_targets()["FaceAngleX"]))
    assert max(samples) > 1.0  # head 分量被保留；若被 body 覆盖，幅度超不过 body_amplitude=1.0

    await _run(ctrl, 0.2)
    assert {name for name, _ in calls} == {"FaceAngleX", "FaceAngleY", "FaceAngleZ"}


def test_merge_target_adds_clamps_and_ignores_blank_names() -> None:
    ctrl, _ = _controller(head_amplitude=3.0, body_amplitude=1.0)
    targets = {"HeadX": 2.0}
    ctrl._merge_target(targets, "HeadX", 2.0)
    assert targets["HeadX"] == 4.0  # 叠加后恰到上限 3.0+1.0
    ctrl._merge_target(targets, "HeadX", 2.0)
    assert targets["HeadX"] == 4.0  # 再叠加被钳制
    ctrl._merge_target(targets, "BodyX", -0.5)
    assert targets["BodyX"] == -0.5  # 未被占用的参数名直接写入
    ctrl._merge_target(targets, "", 1.0)
    assert "" not in targets


async def test_extra_params_sway_within_their_own_amplitude_and_zero_on_stop() -> None:
    ctrl, calls = _controller(head_amplitude=7.0, body_amplitude=5.0, extra_params={"SleeveRX": 3.0, "SleeveRY": 2.0})
    targets = ctrl._compute_targets()
    assert abs(targets["SleeveRX"]) <= 3.0 and abs(targets["SleeveRY"]) <= 2.0

    await _run(ctrl, 0.08)
    assert max(abs(value) for name, value in calls if name == "SleeveRX") <= 3.0
    assert {"SleeveRX", "SleeveRY"} <= {name for name, value in calls if value == 0.0}


def test_axis_wander_is_bounded_moves_and_pauses_randomly() -> None:
    wander = AxisWander(random.Random(42), min_duration=0.5, max_duration=1.5, pause_probability=0.8, max_pause=1.0)
    values = [wander.value(k * 0.05) for k in range(400)]  # 20 秒
    assert all(-1.0 <= v <= 1.0 for v in values) and max(values) - min(values) > 0.3

    run = longest = 1
    for a, b in zip(values, values[1:], strict=False):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    assert longest >= 3  # 存在随机停留（连续不变的时间段）


def test_baseline_params_hold_when_idle_and_yield_to_speech_and_intent() -> None:
    ctrl, _ = _controller(rng=random.Random(1))
    ctrl.set_baseline_params({"MouthSmile": 0.3})
    assert ctrl._compute_targets(speaking=False)["MouthSmile"] == 0.3
    assert "MouthSmile" not in ctrl._compute_targets(speaking=True)  # 说话时表情由口型渲染接管

    ctrl.set_baseline_overrides({"MouthSmile": 1.0})  # Intent 占用该参数（如 happy）
    assert "MouthSmile" not in ctrl._compute_targets(speaking=False)
    ctrl.set_baseline_overrides({})
    assert ctrl._compute_targets(speaking=False)["MouthSmile"] == 0.3


def test_mark_failed_params_presets_stop_writes() -> None:
    ctrl, _ = _controller()
    ctrl.mark_failed_params(["TorsoX", ""])
    assert "TorsoX" in ctrl._failed_params and "" not in ctrl._failed_params


@pytest.mark.parametrize(
    ("name", "last", "value", "prev", "delta_s", "expected"),
    [
        ("MouthSmile", 1000.0, 0.3, 0.3, 0.1, True),  # 基线参数不受节流，每 tick 都写
        ("FaceAngleX", 1000.0, 1.5, 1.5, _PARAM_REFRESH_INTERVAL_S * 0.5, False),  # 值稳定且刚写过：跳过
        ("FaceAngleX", 1000.0, 1.5, 1.5, _PARAM_REFRESH_INTERVAL_S + 0.01, True),  # 超过保鲜间隔：补写防瞬移
        ("FaceAngleX", 1000.0, 1.5, 1.4, 0.05, True),  # 值在变化：正常写
        ("FaceAngleX", 1000.0, 0.0005, 0.0005, 0.05, True),  # 近零维持：每 tick 写，让模型稳在中心
    ],
)
def test_should_write_respects_vts_one_second_expiry(name, last, value, prev, delta_s, expected) -> None:
    assert 0 < _PARAM_REFRESH_INTERVAL_S < 1.0  # 保鲜间隔必须低于 VTS 约 1 秒的注入过期线
    ctrl, _ = _controller()
    ctrl.set_baseline_params({"MouthSmile": 0.3})
    ctrl._last_write[name] = last
    assert ctrl._should_write(name, value, prev, now=last + delta_s) is expected


def test_vts_provider_idle_scale_uses_half_span_and_skips_unknown_names() -> None:
    ranges = {"FaceAngleX": (-30.0, 30.0), "FaceAngleZ": (-90.0, 90.0), "MouthSmile": (0.0, 1.0), "Weird": (0.0, 0.0)}
    scales = VTSProvider._idle_scale_for(ranges, ["FaceAngleX", "FaceAngleZ", "MouthSmile", "Weird", "BodyX", ""])
    # 角度类按原生半幅放大；[0,1] 参数系数 1；零跨度退化为 1；未知与空名跳过（保持原值写入）
    assert scales == {"FaceAngleX": 30.0, "FaceAngleZ": 90.0, "MouthSmile": 1.0, "Weird": 1.0}
