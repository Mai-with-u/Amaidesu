"""MssScreenCapture（mock mss）：显示器枚举与 is_primary 派生、区域换算与越界 clamp、monitor_index 回退、等比缩放、异常降级、线程 DPI 上下文"""

from __future__ import annotations

import ctypes
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.modules.vision.mss_capture as mss_capture_module
from src.modules.vision import MonitorInfo, MssScreenCapture
from src.modules.vision.mss_capture import _derive_is_primary, _per_monitor_dpi_thread

# 三屏布局：monitors[0] 是虚拟合屏；index=1 右侧副屏，index=2 主屏（含原点），index=3 左侧副屏
VIRTUAL = {"left": -1920, "top": 0, "width": 6400, "height": 1600}
RIGHT = {"left": 2560, "top": 0, "width": 1920, "height": 1080}
PRIMARY = {"left": 0, "top": 0, "width": 2560, "height": 1600}
LEFT = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
THREE_SCREENS = [VIRTUAL, RIGHT, PRIMARY, LEFT]


class FakeSct:
    """mock ``mss.mss()`` 上下文管理器（带 .monitors / .grab）。"""

    def __init__(self, monitors: list[dict[str, int]], grab_error: Exception | None = None) -> None:
        self.monitors = monitors
        self._grab_error = grab_error
        self.last_grab: dict[str, int] | None = None

    def __enter__(self) -> "FakeSct":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def grab(self, monitor: dict[str, int]) -> Any:
        self.last_grab = dict(monitor)
        if self._grab_error is not None:
            raise self._grab_error
        w, h = int(monitor["width"]), int(monitor["height"])
        return SimpleNamespace(size=(w, h), bgra=b"\xff\x00\x00\xff" * (w * h))  # 每像素 BGRX 4 字节


def _fake_mss(monkeypatch, monitors, **kwargs) -> FakeSct:
    sct = FakeSct(monitors, **kwargs)
    monkeypatch.setattr(mss_capture_module, "_mss", SimpleNamespace(mss=lambda: sct), raising=False)
    return sct


@pytest.mark.parametrize(
    ("index", "left", "top", "width", "height", "expected"),
    [
        (2, 0, 0, 1920, 1080, True),  # 包含原点的物理屏是主屏
        (1, -1920, 0, 1920, 1080, False),  # 左侧副屏
        (1, 1920, 0, 1920, 1080, False),  # 右侧副屏
        (0, -1920, 0, 6400, 1600, False),  # monitors[0] 虚拟合屏，即使包围矩形含原点也不算物理主屏
    ],
)
def test_is_primary_derivation(index, left, top, width, height, expected) -> None:
    assert _derive_is_primary(index, left, top, width, height) is expected


def test_list_monitors_enumerates_with_primary_and_degrades_without_mss(monkeypatch) -> None:
    _fake_mss(monkeypatch, THREE_SCREENS)
    monitors = MssScreenCapture().list_monitors()
    assert [m.index for m in monitors] == [0, 1, 2, 3]
    assert [m.is_primary for m in monitors] == [False, False, True, False]

    monkeypatch.setattr(mss_capture_module, "_mss", None, raising=False)  # import mss 失败
    assert MssScreenCapture().list_monitors() == []

    def boom():
        raise RuntimeError("no display")

    monkeypatch.setattr(mss_capture_module, "_mss", SimpleNamespace(mss=boom), raising=False)
    assert MssScreenCapture().list_monitors() == []


@pytest.mark.parametrize(
    ("monitor_index", "region", "grab"),
    [
        (2, (10, 10, 110, 110), {"left": 10, "top": 10, "width": 100, "height": 100}),  # 相对显示器 → 虚拟桌面绝对坐标
        (1, (0, 0, 100, 50), {"left": 2560, "top": 0, "width": 100, "height": 50}),  # 叠加显示器原点
        (3, (0, 0, 50, 50), {"left": -1920, "top": 0, "width": 50, "height": 50}),  # 负坐标显示器
        (2, (110, 110, 10, 10), {"left": 10, "top": 10, "width": 100, "height": 100}),  # 写反方向的坐标归一化
        (2, None, {"left": 0, "top": 0, "width": 2560, "height": 1600}),  # 无区域 = 整屏
        (2, (-100, -100, 200, 200), {"left": 0, "top": 0, "width": 200, "height": 200}),  # 负起点 clamp 到显示器内
        (1, (1000, 500, 9999, 9999), {"left": 3560, "top": 500, "width": 920, "height": 580}),  # 单侧越界
        (2, (0, 0, 99999, 99999), {"left": 0, "top": 0, "width": 2560, "height": 1600}),  # 远越界 clamp 到边界
        (
            2,
            (99999, 99999, 99999, 99999),
            {"left": 0, "top": 0, "width": 2560, "height": 1600},
        ),  # clamp 后零尺寸 → 退化为整屏
    ],
)
def test_region_translation_normalization_and_clamp(monkeypatch, monitor_index, region, grab) -> None:
    sct = _fake_mss(monkeypatch, THREE_SCREENS)
    result = MssScreenCapture().capture(monitor_index=monitor_index, region=region)
    assert result.image is not None and sct.last_grab == grab
    assert (result.width, result.height) == (grab["width"], grab["height"])


def test_oversize_region_logs_a_clamp_warning(monkeypatch, loguru_capture) -> None:
    _fake_mss(monkeypatch, THREE_SCREENS)
    MssScreenCapture().capture(monitor_index=2, region=(0, 0, 99999, 99999))
    assert any("clamp" in rec["message"].lower() for rec in loguru_capture.records)


def test_invalid_monitor_index_falls_back_to_first_physical_never_the_virtual_screen(
    monkeypatch, loguru_capture
) -> None:
    sct = _fake_mss(monkeypatch, THREE_SCREENS)
    assert MssScreenCapture().capture(monitor_index=99, region=None).image is not None
    assert sct.last_grab == RIGHT  # 首个物理显示器 index=1，严禁回退到 monitors[0] 虚拟合屏
    assert any("monitor_index=99" in rec["message"] for rec in loguru_capture.records)

    only_virtual = _fake_mss(monkeypatch, [VIRTUAL])
    assert (
        MssScreenCapture().capture(monitor_index=99, region=None).image is not None
    )  # 极端情况：无物理显示器才回退 monitors[0]
    assert only_virtual.last_grab == VIRTUAL


@pytest.mark.parametrize(
    ("max_width", "expected"),
    [(320, (320, 200)), (2560, (2560, 1600)), (None, (2560, 1600)), (0, (2560, 1600)), (100, (100, 62))],
)
def test_max_width_scales_proportionally_only_when_smaller_than_image(monkeypatch, max_width, expected) -> None:
    _fake_mss(monkeypatch, [{"left": 0, "top": 0, "width": 2560, "height": 1600}])
    result = MssScreenCapture().capture(monitor_index=0, region=None, max_width=max_width)
    assert result.image is not None
    assert result.width == expected[0] and abs(result.height - expected[1]) <= 1  # 100 宽时 62.5 取整可能为 62 或 63


def test_capture_failures_return_empty_result_without_raising(monkeypatch) -> None:
    monitors = [{"left": 0, "top": 0, "width": 800, "height": 600}]
    _fake_mss(monkeypatch, monitors, grab_error=RuntimeError("screen permission denied"))
    grab_failed = MssScreenCapture().capture(monitor_index=0)
    assert grab_failed.image is None and isinstance(grab_failed.captured_at_ms, int) and grab_failed.captured_at_ms > 0

    monkeypatch.setattr(mss_capture_module, "_mss", None, raising=False)
    monkeypatch.setattr(mss_capture_module, "_MSS_IMPORT_ERROR", ImportError("no mss"), raising=False)
    unavailable = MssScreenCapture().capture(monitor_index=1)
    assert unavailable.image is None and unavailable.captured_at_ms > 0

    from PIL import Image

    def boom_save(self, fp, format=None, **kwargs):
        raise OSError("encode failed")

    monkeypatch.setattr(Image.Image, "save", boom_save)  # PNG 编码异常
    _fake_mss(monkeypatch, monitors)
    assert MssScreenCapture().capture(monitor_index=0).image is None


def test_result_timestamp_and_region_are_relative_to_the_monitor(monkeypatch) -> None:
    _fake_mss(monkeypatch, [{"left": 2560, "top": 0, "width": 1920, "height": 1080}])
    before = int(time.time() * 1000)
    result = MssScreenCapture().capture(monitor_index=0, region=(10, 10, 110, 110))
    assert before <= result.captured_at_ms <= int(time.time() * 1000)
    assert result.region == [10, 10, 110, 110]  # 输出的是相对显示器坐标，不是虚拟桌面绝对坐标
    assert MssScreenCapture().capture(monitor_index=0).region is None


# 线程级 DPI 上下文（混合 DPI 坐标自洽）


@pytest.fixture
def dpi_api(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """模拟线程 DPI 状态，让任意平台都能验证 Windows 分支的切换和恢复。"""
    context = {"current": 17}

    def set_context(value: ctypes.c_void_p) -> int:
        previous = context["current"]
        context["current"] = value.value
        return previous

    user32 = MagicMock()
    user32.SetThreadDpiAwarenessContext.side_effect = set_context
    user32.GetThreadDpiAwarenessContext.side_effect = lambda: context["current"]
    monkeypatch.setattr(mss_capture_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(mss_capture_module.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    return user32


def test_dpi_context_switches_to_per_monitor_and_restores_even_when_nested(dpi_api: MagicMock) -> None:
    per_monitor_v2 = ctypes.c_void_p(-4).value
    before = dpi_api.GetThreadDpiAwarenessContext()
    with _per_monitor_dpi_thread():
        assert dpi_api.GetThreadDpiAwarenessContext() == per_monitor_v2
        with _per_monitor_dpi_thread():  # capture 内 list_monitors + grab 两处嵌套包裹
            assert dpi_api.GetThreadDpiAwarenessContext() == per_monitor_v2
        assert dpi_api.GetThreadDpiAwarenessContext() == per_monitor_v2  # 内层退出不能提前还原外层
    assert dpi_api.GetThreadDpiAwarenessContext() == before and dpi_api.SetThreadDpiAwarenessContext.call_count == 4


def test_dpi_context_is_a_passthrough_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(mss_capture_module.sys, "platform", "linux")
    entered = False
    with _per_monitor_dpi_thread():
        entered = True
    assert entered


def test_enumeration_and_grab_both_run_inside_the_dpi_context(monkeypatch) -> None:
    _fake_mss(monkeypatch, [{"left": 0, "top": 0, "width": 800, "height": 600}])
    real = _per_monitor_dpi_thread
    entered = {"n": 0}

    def counting():
        entered["n"] += 1
        return real()

    monkeypatch.setattr(mss_capture_module, "_per_monitor_dpi_thread", counting)
    cap = MssScreenCapture()
    cap.list_monitors()  # 1 次
    cap.capture(monitor_index=1)  # 内部再调 list_monitors + grab，共 2 次
    assert entered["n"] == 3
    assert isinstance(MonitorInfo, type)  # 包级导出与实现同一对象
