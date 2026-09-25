"""MssScreenCapture 单元测试（mock mss）。

覆盖：
- 显示器枚举（含 monitors[0] 虚拟合屏、is_primary 派生、mss 不可用降级）
- 区域相对显示器换算（+ 坐标反向归一化）
- 越界 clamp + warning
- 非法 monitor_index 回退到 1（首个物理显示器）
- max_width 真等比缩放
- mss 抓取异常 → image=None + captured_at_ms（不抛）
- 编码异常 → image=None
- import mss 失败 → 所有方法安全降级
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

import pytest
from loguru import logger as _loguru_logger

import src.modules.vision.mss_capture as mss_capture_module
from src.modules.vision.mss_capture import (
    MonitorInfo,
    MssScreenCapture,
    _derive_is_primary,
    _per_monitor_dpi_thread,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeSct:
    """mock ``mss.mss()`` 上下文管理器（带 .monitors / .grab）。"""

    def __init__(
        self,
        monitors: List[dict[str, int]],
        grab_pixel: bytes = b"\xff\x00\x00",
        grab_size: tuple[int, int] = (1, 1),
        grab_error: Exception | None = None,
    ) -> None:
        self.monitors = monitors
        self._grab_pixel = grab_pixel
        self._grab_size = grab_size
        self._grab_error = grab_error
        self.last_grab: dict[str, int] | None = None
        self.call_count = 0

    def __enter__(self) -> "_FakeSct":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def grab(self, monitor: dict[str, int]) -> Any:
        self.call_count += 1
        self.last_grab = dict(monitor)
        if self._grab_error is not None:
            raise self._grab_error
        # 返回类 mss 对象的"两字段结构"：size + bgra bytes
        w = int(monitor["width"])
        h = int(monitor["height"])
        # 每像素 4 字节（BGRX）
        total = w * h * 4
        return _FakeSctImg(size=(w, h), bgra=b"\xff\x00\x00\xff" * (w * h) if total else b"")


class _FakeSctImg:
    def __init__(self, size: tuple[int, int], bgra: bytes) -> None:
        self.size = size
        self.bgra = bgra


def _install_fake_mss(monkeypatch, fake_sct: _FakeSct) -> None:
    """把模块内的 _mss 替换为模拟 mss 模块对象：``_mss.mss()`` 返回 _FakeSct。"""

    class _FakeMssModule:
        @staticmethod
        def mss() -> _FakeSct:
            return fake_sct

    fake_module = _FakeMssModule()
    monkeypatch.setattr(mss_capture_module, "_mss", fake_module, raising=False)


@pytest.fixture
def loguru_capture():
    """捕获 loguru 记录（项目用 loguru，pytest caplog 不适用）。"""

    class _Capture:
        def __init__(self) -> None:
            self.records: List[dict] = []
            self._sink_id: Optional[int] = None

        def __enter__(self) -> "_Capture":
            def _sink(message) -> None:
                rec = message.record
                self.records.append(
                    {
                        "level": rec["level"].name,
                        "message": rec["message"],
                        "module": rec["name"],
                    }
                )

            self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
            return self

        def __exit__(self, *exc_info) -> None:
            if self._sink_id is not None:
                _loguru_logger.remove(self._sink_id)
                self._sink_id = None

    cap = _Capture()
    with cap:
        yield cap


# ---------------------------------------------------------------------------
# is_primary 派生
# ---------------------------------------------------------------------------


class TestIsPrimaryDerivation:
    def test_primary_when_origin_inside(self):
        # 主屏：left=0, top=0
        assert _derive_is_primary(2, 0, 0, 1920, 1080) is True

    def test_primary_when_origin_in_upper_left_of_negative_left(self):
        # 副屏位于左侧：left=-1920，主屏仍包含 (0,0)
        assert _derive_is_primary(1, -1920, 0, 1920, 1080) is False
        assert _derive_is_primary(2, 0, 0, 2560, 1600) is True

    def test_not_primary_when_origin_outside(self):
        # 右侧副屏
        assert _derive_is_primary(1, 1920, 0, 1920, 1080) is False

    def test_virtual_all_index_zero_never_primary(self):
        # monitors[0] 是虚拟合屏——即使包围矩形包含 (0,0) 也不算物理主屏
        assert _derive_is_primary(0, -1920, 0, 6400, 1600) is False


# ---------------------------------------------------------------------------
# 显示器枚举
# ---------------------------------------------------------------------------


class TestListMonitors:
    def test_returns_all_monitors_with_indices(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[
                {"left": -1920, "top": 0, "width": 6400, "height": 1600},  # 虚拟合屏
                {"left": 2560, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 2560, "height": 1600},
                {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)

        cap = MssScreenCapture()
        monitors = cap.list_monitors()

        assert len(monitors) == 4
        assert [m.index for m in monitors] == [0, 1, 2, 3]
        # 虚拟合屏 is_primary = False（不包含 (0,0) 单点）
        assert monitors[0].is_primary is False
        # 包含 (0,0) 的物理显示器
        assert monitors[2].is_primary is True
        assert monitors[1].is_primary is False
        assert monitors[3].is_primary is False

    def test_returns_empty_when_mss_unavailable(self, monkeypatch):
        # 模拟 import mss 失败
        monkeypatch.setattr(mss_capture_module, "_mss", None, raising=False)
        cap = MssScreenCapture()
        assert cap.list_monitors() == []

    def test_returns_empty_when_enumeration_fails(self, monkeypatch):
        class _BoomMss:
            @staticmethod
            def mss():
                raise RuntimeError("no display")

        monkeypatch.setattr(mss_capture_module, "_mss", _BoomMss(), raising=False)
        cap = MssScreenCapture()
        assert cap.list_monitors() == []


# ---------------------------------------------------------------------------
# 区域相对显示器换算 + clamp
# ---------------------------------------------------------------------------


class TestRegionCoordinateTranslation:
    def _setup(self, monkeypatch):
        # 三屏布局：index=1 (left=2560, 1920x1080), index=2 (left=0, 2560x1600), index=3 (left=-1920, 1920x1080)
        fake_sct = _FakeSct(
            monitors=[
                {"left": -1920, "top": 0, "width": 6400, "height": 1600},
                {"left": 2560, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 2560, "height": 1600},
                {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        return fake_sct

    def test_region_translates_to_absolute_virtual_desktop(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # index=2 (left=0, top=0, 2560x1600) + region=(10,10,110,110) → grab=(10,10,100,100)
        result = cap.capture(monitor_index=2, region=(10, 10, 110, 110))
        assert result.image is not None
        assert result.width == 100
        assert result.height == 100
        assert fake_sct.last_grab == {"left": 10, "top": 10, "width": 100, "height": 100}

    def test_region_offset_by_monitor_origin(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # index=1 (left=2560, top=0) + region=(0,0,100,50) → grab=(2560,0,100,50)
        result = cap.capture(monitor_index=1, region=(0, 0, 100, 50))
        assert result.image is not None
        assert result.width == 100
        assert result.height == 50
        assert fake_sct.last_grab == {"left": 2560, "top": 0, "width": 100, "height": 50}

    def test_region_offset_negative_left_monitor(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # index=3 (left=-1920) + region=(0,0,50,50) → grab=(-1920,0,50,50)
        result = cap.capture(monitor_index=3, region=(0, 0, 50, 50))
        assert result.image is not None
        assert fake_sct.last_grab == {"left": -1920, "top": 0, "width": 50, "height": 50}

    def test_region_swapped_coords_normalized(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # region=(110,110,10,10) 写反方向 → 应归一化为 (10,10,110,110)
        result = cap.capture(monitor_index=2, region=(110, 110, 10, 10))
        assert result.image is not None
        assert result.width == 100
        assert result.height == 100
        assert fake_sct.last_grab == {"left": 10, "top": 10, "width": 100, "height": 100}

    def test_region_none_uses_full_monitor(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=2, region=None)
        assert result.image is not None
        # mss.grab 收到的是显示器自身 (0,0,2560,1600)
        assert fake_sct.last_grab == {"left": 0, "top": 0, "width": 2560, "height": 1600}
        assert result.width == 2560
        assert result.height == 1600


class TestRegionClamp:
    def _setup(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[
                {"left": -1920, "top": 0, "width": 6400, "height": 1600},
                {"left": 2560, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 2560, "height": 1600},
                {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        return fake_sct

    def test_oversize_region_clamped_with_warning(self, monkeypatch, loguru_capture):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # region 远越界 → clamp 到显示器边界
        with loguru_capture:
            result = cap.capture(monitor_index=2, region=(0, 0, 99999, 99999))
        assert result.image is not None
        assert result.width == 2560
        assert result.height == 1600
        assert fake_sct.last_grab == {"left": 0, "top": 0, "width": 2560, "height": 1600}
        # 至少有一条关于 clamp 的 warning
        assert any("clamp" in rec["message"].lower() for rec in loguru_capture.records)

    def test_partial_oversize_clamps_one_side(self, monkeypatch, loguru_capture):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # index=1 (left=2560, 1920x1080) + region=(1000,500,9999,9999) → clamp 到 (3560,500,920,580)
        with loguru_capture:
            result = cap.capture(monitor_index=1, region=(1000, 500, 9999, 9999))
        assert result.image is not None
        assert fake_sct.last_grab == {"left": 3560, "top": 500, "width": 920, "height": 580}

    def test_negative_origin_clamped_to_monitor(self, monkeypatch):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # index=2 + region=(-100,-100,200,200) → clamp 到 (0,0,200,200)
        result = cap.capture(monitor_index=2, region=(-100, -100, 200, 200))
        assert result.image is not None
        assert fake_sct.last_grab == {"left": 0, "top": 0, "width": 200, "height": 200}

    def test_zero_size_after_clamp_falls_back_to_full(self, monkeypatch, loguru_capture):
        fake_sct = self._setup(monkeypatch)
        cap = MssScreenCapture()

        # region 完全在显示器外（>监视器边界）→ clamp 后 width=0 → fallback to full
        with loguru_capture:
            result = cap.capture(monitor_index=2, region=(99999, 99999, 99999, 99999))
        assert result.image is not None
        # 退化到全屏抓取
        assert fake_sct.last_grab == {"left": 0, "top": 0, "width": 2560, "height": 1600}


# ---------------------------------------------------------------------------
# monitor_index 回退
# ---------------------------------------------------------------------------


class TestInvalidIndexFallback:
    def test_invalid_index_falls_back_to_first_physical(self, monkeypatch, loguru_capture):
        fake_sct = _FakeSct(
            monitors=[
                {"left": -1920, "top": 0, "width": 6400, "height": 1600},
                {"left": 2560, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 2560, "height": 1600},
                {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()

        with loguru_capture:
            result = cap.capture(monitor_index=99, region=None)
        assert result.image is not None
        # 首个物理显示器 = index=1 (2560,0,1920,1080)
        assert fake_sct.last_grab == {"left": 2560, "top": 0, "width": 1920, "height": 1080}
        assert any("monitor_index=99" in rec["message"] for rec in loguru_capture.records)
        # 严禁回退到 monitors[0]
        assert fake_sct.last_grab != {"left": -1920, "top": 0, "width": 6400, "height": 1600}

    def test_only_virtual_monitor_present_falls_back_to_zero(self, monkeypatch, loguru_capture):
        fake_sct = _FakeSct(
            monitors=[
                {"left": -1920, "top": 0, "width": 6400, "height": 1600},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()

        with loguru_capture:
            result = cap.capture(monitor_index=99, region=None)
        # 极端情况：无物理显示器 → 回退到 monitors[0]
        assert result.image is not None


# ---------------------------------------------------------------------------
# max_width 等比缩放
# ---------------------------------------------------------------------------


class TestMaxWidthScaling:
    def _setup(self, monkeypatch, monitor_size=(2560, 1600)):
        fake_sct = _FakeSct(
            monitors=[
                {"left": 0, "top": 0, "width": monitor_size[0], "height": monitor_size[1]},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        return fake_sct

    def test_max_width_scales_down_proportionally(self, monkeypatch):
        self._setup(monkeypatch)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=0, region=None, max_width=320)
        assert result.image is not None
        # 2560x1600 → 320x200 (1600 * 320/2560 = 200)
        assert result.width == 320
        assert result.height == 200

    def test_max_width_equal_to_image_no_scale(self, monkeypatch):
        self._setup(monkeypatch)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=0, region=None, max_width=2560)
        assert result.image is not None
        assert result.width == 2560
        assert result.height == 1600

    def test_max_width_zero_or_none_no_scale(self, monkeypatch):
        self._setup(monkeypatch)
        cap = MssScreenCapture()

        result_none = cap.capture(monitor_index=0, region=None, max_width=None)
        assert result_none.width == 2560
        result_zero = cap.capture(monitor_index=0, region=None, max_width=0)
        assert result_zero.width == 2560

    def test_max_width_smaller_than_image_scales(self, monkeypatch):
        self._setup(monkeypatch)
        cap = MssScreenCapture()

        # max_width 小于图宽 → 缩放
        result = cap.capture(monitor_index=0, region=None, max_width=100)
        assert result.image is not None
        assert result.width == 100
        # 1600 * 100/2560 = 62.5 → round → 62 或 63（取实际计算结果）
        expected_h = round(1600 * 100 / 2560)
        assert abs(result.height - expected_h) <= 1


# ---------------------------------------------------------------------------
# 异常降级
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_mss_grab_exception_returns_empty_result(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[{"left": 0, "top": 0, "width": 1920, "height": 1080}],
            grab_error=RuntimeError("screen permission denied"),
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=0)
        assert result.image is None
        assert result.captured_at_ms > 0
        assert isinstance(result.captured_at_ms, int)

    def test_mss_unavailable_returns_empty_result(self, monkeypatch):
        monkeypatch.setattr(mss_capture_module, "_mss", None, raising=False)
        cap = MssScreenCapture()
        result = cap.capture(monitor_index=1)
        assert result.image is None
        assert result.captured_at_ms > 0

    def test_mss_not_installed_returns_empty_result(self, monkeypatch):
        # 模拟"import mss 失败"：把模块标记成 _mss=None 且 _MSS_IMPORT_ERROR 有值
        monkeypatch.setattr(mss_capture_module, "_mss", None, raising=False)
        monkeypatch.setattr(mss_capture_module, "_MSS_IMPORT_ERROR", ImportError("no mss"), raising=False)
        cap = MssScreenCapture()
        result = cap.capture(monitor_index=1)
        assert result.image is None
        assert result.captured_at_ms > 0

    def test_capture_result_captured_at_ms_recent(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[{"left": 0, "top": 0, "width": 800, "height": 600}],
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()
        before_ms = int(time.time() * 1000)
        result = cap.capture(monitor_index=0)
        after_ms = int(time.time() * 1000)
        assert before_ms <= result.captured_at_ms <= after_ms

    def test_png_encode_failure_returns_empty_result(self, monkeypatch):
        # 通过让 PIL Image.save 抛错验证编码降级
        from PIL import Image

        def _boom_save(self, fp, format=None, **kwargs):
            raise OSError("encode failed")

        monkeypatch.setattr(Image.Image, "save", _boom_save)
        fake_sct = _FakeSct(
            monitors=[{"left": 0, "top": 0, "width": 800, "height": 600}],
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=0)
        assert result.image is None
        assert result.captured_at_ms > 0


# ---------------------------------------------------------------------------
# region 输出语义
# ---------------------------------------------------------------------------


class TestRegionOutputInResult:
    def test_region_output_is_relative_to_monitor(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[
                {"left": 2560, "top": 0, "width": 1920, "height": 1080},
            ]
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()

        result = cap.capture(monitor_index=0, region=(10, 10, 110, 110))
        assert result.image is not None
        # 输出的 region 应当是相对显示器坐标（不是虚拟桌面绝对）
        assert result.region == [10, 10, 110, 110]

    def test_region_none_output_is_none(self, monkeypatch):
        fake_sct = _FakeSct(
            monitors=[{"left": 0, "top": 0, "width": 800, "height": 600}],
        )
        _install_fake_mss(monkeypatch, fake_sct)
        cap = MssScreenCapture()
        result = cap.capture(monitor_index=0)
        assert result.region is None


# ---------------------------------------------------------------------------
# 线程级 DPI 上下文（混合 DPI 坐标自洽）
# ---------------------------------------------------------------------------


class TestPerMonitorDpiThread:
    @staticmethod
    def _current_thread_dpi_context() -> int | None:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetThreadDpiAwarenessContext.argtypes = []
        ctx = user32.GetThreadDpiAwarenessContext()
        return int(ctx) if ctx else None

    def test_context_restored_after_exit(self):
        # Windows 上进出上下文后，线程 DPI 上下文应还原
        before = self._current_thread_dpi_context()
        with _per_monitor_dpi_thread():
            pass
        after = self._current_thread_dpi_context()
        assert before == after

    def test_nested_context_restore(self):
        # 嵌套使用（capture 内 list_monitors + grab 两处包裹）也应正确还原
        before = self._current_thread_dpi_context()
        with _per_monitor_dpi_thread():
            with _per_monitor_dpi_thread():
                pass
        assert before == self._current_thread_dpi_context()

    def test_yields_on_non_windows(self, monkeypatch):
        # 非 Windows 平台：API 缺失时按原行为直通（不抛、正常 yield）
        monkeypatch.setattr(mss_capture_module.sys, "platform", "linux")
        entered = False
        with _per_monitor_dpi_thread():
            entered = True
        assert entered

    def test_list_monitors_and_capture_are_wrapped(self, monkeypatch):
        # 枚举与抓图的 mss 调用都必须在 DPI 上下文内执行
        fake_sct = _FakeSct(
            monitors=[{"left": 0, "top": 0, "width": 800, "height": 600}],
        )
        _install_fake_mss(monkeypatch, fake_sct)

        real_cm = _per_monitor_dpi_thread
        enter_count = {"n": 0}

        def _counting_cm():
            enter_count["n"] += 1
            return real_cm()

        monkeypatch.setattr(mss_capture_module, "_per_monitor_dpi_thread", _counting_cm)

        cap = MssScreenCapture()
        cap.list_monitors()  # 1 次
        cap.capture(monitor_index=1)  # capture 内部再调 list_monitors + grab，共 2 次
        assert enter_count["n"] == 3


# ---------------------------------------------------------------------------
# __init__ 导出
# ---------------------------------------------------------------------------


class TestPublicExport:
    def test_mss_capture_importable_from_vision_package(self):
        from src.modules.vision import MonitorInfo as PKMI
        from src.modules.vision import MssScreenCapture as PKMSC

        assert PKMI is MonitorInfo
        assert PKMSC is MssScreenCapture
