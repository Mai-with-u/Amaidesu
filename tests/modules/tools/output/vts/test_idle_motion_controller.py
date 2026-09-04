"""
IdleMotionController 测试
"""

import asyncio

import pytest

from src.modules.tools.output.vts.idle_motion_controller import AxisWander, IdleMotionController


@pytest.fixture
def controller():
    """创建带模拟回调的 IdleMotionController"""
    calls = []

    async def set_param(name, value):
        calls.append((name, value))
        return True

    return (
        IdleMotionController(
            logger_name="TestIdleMotion",
            is_connected=lambda: True,
            is_speaking=lambda: False,
            set_parameter=set_param,
            param_head_x="HeadX",
            param_head_y="HeadY",
            param_head_z="HeadZ",
            param_body_x="BodyX",
            param_body_y="BodyY",
            param_body_z="BodyZ",
            head_amplitude=0.1,
            body_amplitude=0.05,
            speed=1.0,
            update_interval_ms=20.0,
            fade_speed=0.5,
        ),
        calls,
    )


@pytest.mark.asyncio
async def test_start_stop_and_params_are_sent(controller):
    """启动后应持续发送参数，停止后归零"""
    ctrl, calls = controller
    ctrl.start()
    await asyncio.sleep(0.08)
    await ctrl.stop()

    head_names = {c[0] for c in calls if c[0].startswith("Head")}
    body_names = {c[0] for c in calls if c[0].startswith("Body")}
    assert head_names == {"HeadX", "HeadY", "HeadZ"}
    assert body_names == {"BodyX", "BodyY", "BodyZ"}

    # 停止后参数归零
    zero_values = {c[0]: c[1] for c in calls if c[0] in head_names | body_names and c[1] == 0.0}
    assert len(zero_values) >= 6


@pytest.mark.asyncio
async def test_speaking_pauses_idle(controller):
    """说话期间 idle 应暂停/衰减"""
    speaking = True

    async def set_param(name, value):
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionSpeaking",
        is_connected=lambda: True,
        is_speaking=lambda: speaking,
        set_parameter=set_param,
        head_amplitude=0.1,
        body_amplitude=0.05,
        update_interval_ms=20.0,
        fade_speed=0.5,
    )
    ctrl.start()
    await asyncio.sleep(0.08)
    await ctrl.stop()

    # 只要说话期间不出异常即可；停止时归零
    assert not ctrl._running


@pytest.mark.parametrize(
    "speech_pause_enabled, expect_nonzero",
    [
        (True, False),  # 默认：说话期间暂停 idle
        (False, True),  # 配置为 False：说话期间继续 idle
    ],
)
@pytest.mark.asyncio
async def test_speech_pause_flag(speech_pause_enabled, expect_nonzero):
    """说话时是否暂停 idle 取决于 speech_pause_enabled 开关"""
    speaking = True
    calls = []

    async def set_param(name, value):
        calls.append((name, value))
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionSpeechFlag",
        is_connected=lambda: True,
        is_speaking=lambda: speaking,
        set_parameter=set_param,
        head_amplitude=1.0,
        body_amplitude=1.0,
        update_interval_ms=20.0,
        fade_speed=0.5,
        speech_pause_enabled=speech_pause_enabled,
    )
    ctrl.start()
    await asyncio.sleep(0.6)
    await ctrl.stop()

    max_abs = max((abs(v) for _, v in calls), default=0.0)
    if expect_nonzero:
        assert max_abs > 0.001, "speech_pause_enabled=False 时说话期间仍应继续产生 idle 动作"
    else:
        assert max_abs <= 0.001, "speech_pause_enabled=True 时说话期间 idle 应暂停归零"


@pytest.mark.asyncio
async def test_set_parameter_names_updates_targets():
    """动态更新参数名后，应写入新参数名并重置状态"""
    calls = []

    async def set_param(name, value):
        calls.append((name, value))
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionRename",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=set_param,
        param_head_x="HeadX",
        head_amplitude=1.0,
        update_interval_ms=20.0,
        fade_speed=0.5,
    )

    ctrl.set_parameter_names(param_head_x="NewHeadX", param_head_y="NewHeadY")
    ctrl.start()
    await asyncio.sleep(0.06)
    await ctrl.stop()

    head_names = {c[0] for c in calls if c[0].startswith("NewHead")}
    assert head_names == {"NewHeadX", "NewHeadY"}
    assert "HeadX" not in {c[0] for c in calls}


@pytest.mark.asyncio
async def test_shared_head_body_params_are_merged():
    """head/body 使用相同参数名时，body 信号应叠加合并而不是覆盖 head"""
    import random

    calls = []

    async def set_param(name, value):
        calls.append((name, value))
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionShared",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=set_param,
        param_head_x="FaceAngleX",
        param_head_y="FaceAngleY",
        param_head_z="FaceAngleZ",
        param_body_x="FaceAngleX",
        param_body_y="FaceAngleY",
        param_body_z="FaceAngleZ",
        head_amplitude=3.0,
        body_amplitude=1.0,
        update_interval_ms=20.0,
        fade_speed=0.5,
        rng=random.Random(7),
    )

    targets = ctrl._compute_targets()
    # 同名参数合并后只出现一次
    assert set(targets.keys()) == {"FaceAngleX", "FaceAngleY", "FaceAngleZ"}
    # 合并值不超过钳制上限 head_amplitude + body_amplitude
    limit = 3.0 + 1.0
    for value in targets.values():
        assert abs(value) <= limit

    # 采样多个时间点：合并后应保留 head 分量（若被 body 覆盖，幅度超不过 body_amplitude=1.0）
    import time as _time

    samples = []
    for k in range(20):
        ctrl._start_time = _time.time() - k * 1.3
        samples.append(abs(ctrl._compute_targets()["FaceAngleX"]))
    assert max(samples) > 1.0, "head/body 共享参数时应保留 head 分量，而不是被 body 覆盖"

    # 端到端：循环写入时每个共享参数只写一次，且停止后归零
    ctrl.start()
    await asyncio.sleep(0.6)
    await ctrl.stop()

    names = {c[0] for c in calls}
    assert names == {"FaceAngleX", "FaceAngleY", "FaceAngleZ"}


def test_merge_target_only_combines_existing_names():
    """_merge_target：参数名未被 head 占用时保持原行为（直接写入）"""

    async def set_param(name, value):
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionMerge",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=set_param,
        head_amplitude=3.0,
        body_amplitude=1.0,
    )

    targets = {"HeadX": 2.0}
    # 已存在的参数名：叠加并钳制
    ctrl._merge_target(targets, "HeadX", 2.0)
    assert targets["HeadX"] == 4.0  # 未超上限 3.0+1.0，直接相加
    ctrl._merge_target(targets, "HeadX", 2.0)
    assert targets["HeadX"] == 4.0  # 超过上限，钳制到 4.0
    # 未占用的参数名：直接写入（可为负）
    ctrl._merge_target(targets, "BodyX", -0.5)
    assert targets["BodyX"] == -0.5
    # 空参数名忽略
    ctrl._merge_target(targets, "", 1.0)
    assert "" not in targets


@pytest.mark.asyncio
async def test_extra_params_are_swayed_independently():
    """extra_params（如袖子参数）应参与 idle 摆动，幅度独立且不超过配置值"""
    calls = []

    async def set_param(name, value):
        calls.append((name, value))
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionExtra",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=set_param,
        head_amplitude=7.0,
        body_amplitude=5.0,
        update_interval_ms=20.0,
        fade_speed=0.5,
        extra_params={"SleeveRX": 3.0, "SleeveRY": 2.0},
    )

    targets = ctrl._compute_targets()
    assert "SleeveRX" in targets and "SleeveRY" in targets
    assert abs(targets["SleeveRX"]) <= 3.0
    assert abs(targets["SleeveRY"]) <= 2.0

    ctrl.start()
    await asyncio.sleep(0.08)
    await ctrl.stop()

    sleeve_values = [abs(v) for n, v in calls if n == "SleeveRX"]
    assert sleeve_values, "SleeveRX 应被持续写入"
    assert max(sleeve_values) <= 3.0
    # 停止后归零
    zero_names = {n for n, v in calls if v == 0.0}
    assert "SleeveRX" in zero_names and "SleeveRY" in zero_names


def test_axis_wander_irregular_rhythm():
    """AxisWander：输出有界、存在随机停留（连续不变），且整体有运动"""
    import random

    w = AxisWander(
        random.Random(42),
        min_duration=0.5,
        max_duration=1.5,
        pause_probability=0.8,
        max_pause=1.0,
    )
    dt = 0.05
    values = [w.value(k * dt) for k in range(400)]  # 20 秒

    # 输出始终在归一化范围内
    assert all(-1.0 <= v <= 1.0 for v in values)
    # 有运动
    assert max(values) - min(values) > 0.3
    # 存在停留：至少一段连续 3 个采样值完全相同（pause 期间保持目标值）
    run = 1
    max_run = 1
    for a, b in zip(values, values[1:]):
        run = run + 1 if a == b else 1
        max_run = max(max_run, run)
    assert max_run >= 3, "应存在随机停留（连续不变的时间段）"


def test_baseline_params_maintained_and_skipped():
    """常驻基线参数：闲置时写入；说话或被 Intent 占用时跳过"""
    import random

    async def set_param(name, value):
        return True

    ctrl = IdleMotionController(
        logger_name="TestIdleMotionBaseline",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=set_param,
        rng=random.Random(1),
    )
    ctrl.set_baseline_params({"MouthSmile": 0.3})

    # 闲置：基线写入
    targets = ctrl._compute_targets(speaking=False)
    assert targets["MouthSmile"] == 0.3

    # 说话：基线跳过（表情由 LipSync 接管）
    targets = ctrl._compute_targets(speaking=True)
    assert "MouthSmile" not in targets

    # Intent 占用 MouthSmile（如 happy=1.0）：基线跳过
    ctrl.set_baseline_overrides({"MouthSmile": 1.0})
    targets = ctrl._compute_targets(speaking=False)
    assert "MouthSmile" not in targets

    # Intent 清空（neutral）：基线恢复
    ctrl.set_baseline_overrides({})
    targets = ctrl._compute_targets(speaking=False)
    assert targets["MouthSmile"] == 0.3
