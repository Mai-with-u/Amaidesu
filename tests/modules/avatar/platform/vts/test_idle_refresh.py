"""IdleMotionController._should_write 注入保鲜决策单测。

背景：VTS 要求受控参数至少每秒重发一次，否则参数回退到默认值（模型
瞬移）。idle 的写入节流（值稳定即跳过）必须让位于该保鲜约束。
"""

from __future__ import annotations

from src.modules.avatar.platform.vts.idle_motion_controller import (
    _PARAM_REFRESH_INTERVAL_S,
    IdleMotionController,
)


def _build() -> IdleMotionController:
    async def _fake_set_parameter(name: str, value: float) -> bool:
        return True

    return IdleMotionController(
        logger_name="test.idle",
        is_connected=lambda: True,
        is_speaking=lambda: False,
        set_parameter=_fake_set_parameter,
    )


def test_refresh_interval_is_below_vts_one_second_expiry() -> None:
    """保鲜间隔必须低于 VTS 约 1 秒的注入过期线，留足余量。"""
    assert 0 < _PARAM_REFRESH_INTERVAL_S < 1.0


def test_baseline_param_always_writes() -> None:
    """基线参数（如 MouthSmile）不受节流影响，每 tick 都写。"""
    controller = _build()
    controller.set_baseline_params({"MouthSmile": 0.3})
    controller._last_write["MouthSmile"] = 1000.0
    assert controller._should_write("MouthSmile", 0.3, 0.3, now=1000.1)


def test_steady_value_within_interval_is_skipped() -> None:
    """值稳定且刚写入过：沿用节流，跳过写入。"""
    controller = _build()
    controller._last_write["FaceAngleX"] = 1000.0
    assert not controller._should_write("FaceAngleX", 1.5, 1.5, now=1000.0 + _PARAM_REFRESH_INTERVAL_S * 0.5)


def test_steady_value_past_interval_is_refreshed() -> None:
    """值稳定但超过保鲜间隔：必须补写，防止 VTS 过期归零导致瞬移。"""
    controller = _build()
    controller._last_write["FaceAngleX"] = 1000.0
    assert controller._should_write("FaceAngleX", 1.5, 1.5, now=1000.0 + _PARAM_REFRESH_INTERVAL_S + 0.01)


def test_changing_value_always_writes() -> None:
    """值在变化（差值达阈值）：正常写入，与保鲜无关。"""
    controller = _build()
    controller._last_write["FaceAngleX"] = 1000.0
    assert controller._should_write("FaceAngleX", 1.5, 1.4, now=1000.05)


def test_near_zero_value_always_writes() -> None:
    """近零维持：每 tick 写入，让模型稳定停在中心位。"""
    controller = _build()
    controller._last_write["FaceAngleX"] = 1000.0
    assert controller._should_write("FaceAngleX", 0.0005, 0.0005, now=1000.05)
