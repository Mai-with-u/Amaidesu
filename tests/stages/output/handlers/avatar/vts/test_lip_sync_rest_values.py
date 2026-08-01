"""
LipSyncProcessor 表情静止值（rest values）测试

说话结束后，表情参数应淡出到静止值（EyeOpen=1.0 睁眼、MouthSmile=常驻微笑），
而不是一律归零（归零会导致闭眼/撇嘴约 1 秒才恢复）。
"""

import pytest

from src.stages.output.handlers.avatar.vts.lip_sync_processor import LipSyncProcessor


@pytest.fixture
def processor():
    """创建带模拟回调与静止值的 LipSyncProcessor"""
    calls = []

    async def set_param(name, value, weight=1):
        calls.append((name, value))
        return True

    proc = LipSyncProcessor(
        logger_name="TestLipSync",
        sample_rate=16000,
        volume_threshold=0.01,
        smoothing_factor=0.3,
        vowel_detection_sensitivity=0.5,
        vts_set_parameter=set_param,
        is_connected=lambda: True,
        expression_rest_values={"MouthSmile": 0.3, "EyeOpenLeft": 1.0, "EyeOpenRight": 1.0},
    )
    return proc, calls


@pytest.mark.asyncio
async def test_stop_session_returns_to_rest_values(processor):
    """stop_session 后 MouthOpen 归零，表情参数回到静止值而不是 0"""
    proc, calls = processor
    proc.set_base_expressions({"MouthSmile": 1.0, "EyeOpenLeft": 1.0, "MouthOpen": 0.5})

    await proc.start_session("测试")
    # 模拟说话期间基础表情被推到目标值
    proc._current_expression_values["MouthSmile"] = 1.0
    proc._current_expression_values["EyeOpenLeft"] = 1.0
    await proc.stop_session()

    last_values = {}
    for name, value in calls:
        last_values[name] = value

    # MouthOpen 被 set_base_expressions 过滤，最终闭嘴
    assert last_values.get("MouthOpen") == 0.0
    # 表情参数淡出到静止值而不是 0
    assert last_values.get("MouthSmile") == pytest.approx(0.3, abs=0.01)
    # EyeOpenLeft 静止值就是 1.0（保持睁眼，不会被拉到 0）
    assert last_values.get("EyeOpenLeft", 1.0) == pytest.approx(1.0, abs=0.01)
    # 整个淡出过程中 EyeOpenLeft 不应出现接近 0（闭眼）的值
    eye_values = [v for n, v in calls if n == "EyeOpenLeft"]
    assert all(v > 0.5 for v in eye_values)


def test_has_active_expressions_compares_against_rest(processor):
    """_has_active_expressions 应与静止值比较：等于静止值即视为不活跃"""
    proc, _ = processor
    proc._current_expression_values = {"MouthSmile": 0.3, "EyeOpenLeft": 1.0}
    assert not proc._has_active_expressions()

    proc._current_expression_values = {"MouthSmile": 0.8, "EyeOpenLeft": 1.0}
    assert proc._has_active_expressions()

    # 未配置静止值的参数仍以 0 为基准
    proc._current_expression_values = {"Brows": 0.0}
    assert not proc._has_active_expressions()
    proc._current_expression_values = {"Brows": 0.5}
    assert proc._has_active_expressions()


@pytest.mark.asyncio
async def test_fade_targets_rest_values_when_not_speaking(processor):
    """静音/结束时 _update_base_expressions 的淡出目标是静止值"""
    proc, calls = processor
    proc.set_base_expressions({"MouthSmile": 1.0})
    proc._current_expression_values["MouthSmile"] = 1.0
    proc.is_speaking = False

    await proc._update_base_expressions(volume=0.0)

    # 当前值应向静止值 0.3 移动，而不是向 0 移动
    current = proc._current_expression_values["MouthSmile"]
    assert current < 1.0
    assert current > 0.3


def test_default_rest_value_is_zero():
    """不传 expression_rest_values 时，静止值默认为 0（向后兼容）"""

    async def set_param(name, value, weight=1):
        return True

    proc = LipSyncProcessor(
        logger_name="TestLipSyncDefault",
        sample_rate=16000,
        volume_threshold=0.01,
        smoothing_factor=0.3,
        vowel_detection_sensitivity=0.5,
        vts_set_parameter=set_param,
        is_connected=lambda: True,
    )
    assert proc._rest_value("MouthSmile") == 0.0
    assert proc._rest_value("EyeOpenLeft") == 0.0
