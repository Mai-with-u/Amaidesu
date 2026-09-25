"""mss 多显示器抓屏后端 —— ``ScreenCapture`` 协议的生产实现。

## 坐标语义（mss 库的客观约定，本机实测确认）

mss 库以"**虚拟桌面绝对坐标**"表达显示器位置：

- ``mss.mss().monitors`` 是一个列表：
  - ``monitors[0]`` 是**所有物理显示器的最小包围矩形**（拼合虚拟大桌面）；
    本机实测：``left=-1920, top=0, width=6400, height=1600``，覆盖三屏拼合。
    该条目**不可**作为降级目标——它不等同任何单台显示器。
  - ``monitors[1..N]`` 是**物理显示器**（每条对应一台真实显示器），
    本机实测：index=1/2/3 三条。每条 ``left/top`` 是该显示器左上角在
    虚拟桌面坐标系中的绝对位置（可能为负，例如副屏位于主屏左侧时）。
- mss 的 ``grab`` 接口接收的 ``monitor`` 字典使用同样的虚拟桌面坐标，
  截取范围由 ``left/top/width/height`` 限定。
- ``list_monitors()`` 中 ``is_primary`` 由本模块**派生**判定：
  包含虚拟桌面 ``(0, 0)`` 点的物理显示器视为主屏（与 Windows"主显示器"
  概念一致）。mss 本身不输出该字段。

## 设计要点

- 直接调 mss，**不**封装 CaptureService 抽象层（任务边界内约定）。
- ``region`` 入参相对**所选显示器左上角**，内部换算为虚拟桌面绝对坐标
  （``mon.left + x1`` 等），再交给 mss ``grab``。
- 越界 ``region`` → 裁剪 + warning + 返回裁剪后尺寸（不抛）。
- 显示器热插拔：每次调用 ``list_monitors()`` / ``capture()`` 重新枚举一次。
- 失败（``import mss`` 失败 / 抓取异常 / mss 内部错）→ 返回
  ``image=None`` + ``captured_at_ms``（不抛），沿用既有"空快照"语义。
- 单屏降级：``monitor_index`` 不存在 → 回退到 ``1``（首个物理显示器）+
  warning；**不得**回退到 ``0=合屏``。
- 混合 DPI 自洽：枚举与抓图均在线程级 per-monitor DPI 上下文内执行
  （``_per_monitor_dpi_thread``），使 mss 枚举坐标与 BitBlt 抓图恒为同一
  物理像素空间——进程级感知被其他组件（如 tkinter）设为 system-aware 时
  副屏不发生坐标虚拟化错位。
- 真缩放：``max_width`` 非 None 且图像宽 > ``max_width`` 时用 PIL 等比
  缩放（高按比例）。
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from PIL import Image

from src.modules.logging import get_logger
from src.modules.vision.look_at_screen import ScreenCaptureResult

logger = get_logger("MssScreenCapture")

# mss 是可选/可失败的（缺包 / 系统层不可用），允许 import 失败
try:
    import mss as _mss  # type: ignore[import-not-found]
except Exception as exc:  # noqa: BLE001 - 后端边界：缺包/系统层不可用走降级
    _mss = None  # type: ignore[assignment]
    _MSS_IMPORT_ERROR: Optional[BaseException] = exc
else:
    _MSS_IMPORT_ERROR = None

# Win10 1607+ 的线程级 DPI 上下文句柄（-4 = PER_MONITOR_AWARE_V2 伪句柄）
_DPI_CONTEXT_PER_MONITOR_V2 = -4


@contextlib.contextmanager
def _per_monitor_dpi_thread() -> Iterator[None]:
    """线程级切换到 per-monitor DPI 感知，保证枚举坐标与 BitBlt 抓图同为物理像素。

    mss 的显示器枚举坐标跟随调用线程的 DPI 感知上下文做虚拟化，而 BitBlt
    抓屏 DC 恒按物理像素取数。当进程 DPI 感知被其他组件（如 tkinter 字幕窗）
    抢先设为 system-aware、且主屏缩放与副屏不一致（混合 DPI）时，副屏枚举
    坐标会被按"系统 DPI / 屏幕 DPI"放大，抓图区域随之错位（内容偏移 + 黑边）。
    线程级上下文只影响本线程的枚举/抓图调用，不改动进程内其他组件（Tk 窗口
    等）的感知设置；API 缺失（早于 Win10 1607）或切换失败时保持调用线程
    原有行为。
    """
    user32 = None
    prev: Optional[int] = None
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(_DPI_CONTEXT_PER_MONITOR_V2))
        except Exception as exc:  # noqa: BLE001 - 上下文不可用则保持原行为
            logger.debug(f"线程 DPI 上下文切换不可用，按调用线程默认行为继续: {exc}")
            user32 = None
            prev = None
    try:
        yield
    finally:
        if prev and user32 is not None:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


@dataclass(slots=True)
class MonitorInfo:
    """单个显示器的元数据。

    Attributes:
        index: mss 枚举中的索引（1..N 为物理显示器；0=虚拟合屏）。
        left/top: 该显示器左上角在虚拟桌面坐标系中的绝对位置（像素）。
        width/height: 显示器的像素尺寸。
        is_primary: 是否为系统主屏（包含虚拟桌面 (0,0) 点）。
    """

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool


def _derive_is_primary(monitor_index: int, left: int, top: int, width: int, height: int) -> bool:
    """判断显示器是否包含虚拟桌面 (0, 0)——即系统主屏位置。

    mss 不输出 ``is_primary`` 字段；按 Windows / X11 主屏约定（任务栏
    起点在 (0, 0)）做派生。半开区间 ``[left, left+width)``。

    ``monitors[0]`` 是 mss 虚拟合屏条目，**不算物理显示器**——即使其
    包围矩形包含 (0, 0)，也跳过判定（主屏归属在物理显示器侧）。
    """
    if monitor_index == 0:
        return False
    return left <= 0 < left + width and top <= 0 < top + height


class MssScreenCapture:
    """基于 ``mss`` 库的多显示器抓屏后端。

    Example:
        >>> cap = MssScreenCapture()
        >>> for mon in cap.list_monitors():
        ...     print(mon.index, mon.width, mon.height, mon.is_primary)
        >>> result = cap.capture(monitor_index=1, region=(0, 0, 800, 600))
    """

    def __init__(self) -> None:
        self._import_error: Optional[BaseException] = _MSS_IMPORT_ERROR

    def _ensure_mss(self) -> bool:
        """确认 mss 可用。失败 → log + 返回 False（让 ``capture`` 走降级）。"""
        if _mss is None:
            logger.warning(
                f"mss 不可用（import 失败: {type(self._import_error).__name__ if self._import_error else 'unknown'}: "
                f"{self._import_error}）；返回空快照"
            )
            return False
        return True

    def list_monitors(self) -> List[MonitorInfo]:
        """枚举当前所有显示器（含虚拟合屏 monitors[0]）。

        Returns:
            显示器列表，按 mss 枚举顺序排列（index=0 虚拟合屏，1..N 物理）。
            mss 不可用时返回空列表。
        """
        if not self._ensure_mss():
            return []
        try:
            with _per_monitor_dpi_thread(), _mss.mss() as sct:  # type: ignore[misc]
                raw_monitors = sct.monitors
        except Exception as exc:  # noqa: BLE001 - 后端边界：枚举失败走降级
            logger.warning(f"mss 枚举显示器失败: {type(exc).__name__}: {exc}")
            return []

        result: List[MonitorInfo] = []
        for idx, mon in enumerate(raw_monitors):
            left = int(mon.get("left", 0))
            top = int(mon.get("top", 0))
            width = int(mon.get("width", 0))
            height = int(mon.get("height", 0))
            result.append(
                MonitorInfo(
                    index=idx,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    is_primary=_derive_is_primary(idx, left, top, width, height),
                )
            )
        return result

    def _select_monitor(
        self,
        monitors: List[MonitorInfo],
        monitor_index: int,
    ) -> Tuple[MonitorInfo, Optional[MonitorInfo]]:
        """按索引选显示器；非法 index → 回退到 1（首个物理显示器）+ warning。

        Args:
            monitors: ``list_monitors()`` 的输出。
            monitor_index: 期望的显示器索引。

        Returns:
            ``(actual, fallback_used)``：选中显示器与是否触发回退。
            ``fallback_used`` 为 None 表示无回退；否则为原 ``monitor_index``。
        """
        physical = [m for m in monitors if m.index >= 1]
        # 直接命中（含 monitors[0] 虚拟合屏——若调用方显式要求该 index，也接受）
        for m in monitors:
            if m.index == monitor_index:
                return m, None
        # 非法 → 回退到首个物理显示器（**不得**回退到 monitors[0] 合屏）
        fallback_used = monitor_index
        if physical:
            logger.warning(f"monitor_index={monitor_index} 不存在；回退到首个物理显示器 index={physical[0].index}")
            return physical[0], fallback_used
        # 连物理显示器都没有（极端情况：只有 monitors[0] 虚拟合屏）→ 用 0 兜底
        if monitors:
            logger.warning(f"monitor_index={monitor_index} 不存在且无物理显示器；回退到 monitors[0] 虚拟合屏")
            return monitors[0], fallback_used
        # 全部缺失
        return MonitorInfo(index=1, left=0, top=0, width=0, height=0, is_primary=True), fallback_used

    def _resolve_region(
        self,
        monitor: MonitorInfo,
        region: Optional[Tuple[int, int, int, int]],
    ) -> Optional[Tuple[int, int, int, int]]:
        """把"相对显示器左上角"的 region 换算为 mss 虚拟桌面绝对坐标，并 clamp。

        Args:
            monitor: 选中的显示器（``list_monitors()`` 项）。
            region: 相对显示器左上角的 ``(x1, y1, x2, y2)``；None = 全屏。

        Returns:
            换算并 clamp 后的 ``(abs_left, abs_top, width, height)``；
            None = 抓取整个显示器（mss.grab 用 monitor 字典表达）。
        """
        if region is None:
            return None
        try:
            rx1, ry1, rx2, ry2 = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        except (TypeError, ValueError, IndexError):
            logger.warning(f"region 参数非法（{region}）；按全屏抓取")
            return None

        # 归一化（允许 region 写反方向 → 交换）
        if rx2 < rx1:
            rx1, rx2 = rx2, rx1
        if ry2 < ry1:
            ry1, ry2 = ry2, ry1

        abs_left = monitor.left + rx1
        abs_top = monitor.top + ry1
        abs_right = monitor.left + rx2
        abs_bottom = monitor.top + ry2

        mon_right = monitor.left + monitor.width
        mon_bottom = monitor.top + monitor.height

        # clamp 到显示器边界
        clamped = False
        if abs_left < monitor.left:
            abs_left = monitor.left
            clamped = True
        if abs_top < monitor.top:
            abs_top = monitor.top
            clamped = True
        if abs_right > mon_right:
            abs_right = mon_right
            clamped = True
        if abs_bottom > mon_bottom:
            abs_bottom = mon_bottom
            clamped = True

        width = max(0, abs_right - abs_left)
        height = max(0, abs_bottom - abs_top)
        if clamped:
            logger.warning(
                f"region {region} 超出显示器 index={monitor.index} 边界 "
                f"({monitor.left},{monitor.top},{monitor.width}x{monitor.height})；"
                f"已 clamp 为 ({abs_left},{abs_top},{width}x{height})"
            )
        if width <= 0 or height <= 0:
            logger.warning(f"region {region} clamp 后尺寸为 0；按全屏抓取")
            return None
        return (abs_left, abs_top, width, height)

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """抓取指定显示器（+ 可选区域）的截图。

        Args:
            monitor_index: 显示器索引（1..N 物理；0 = 虚拟合屏；非法 → 回退 1）。
            region: 可选区域 ``(x1, y1, x2, y2)``，**相对所选显示器左上角**；
                None = 抓取整个显示器。
            max_width: 非 None 且图宽 > ``max_width`` 时做 PIL 等比缩放
                （高按比例；返回缩放后的 width/height）；None 或 0 = 不缩放。

        Returns:
            :class:`ScreenCaptureResult`；mss 不可用 / 抓取失败时
            ``image=None`` + ``captured_at_ms``（不抛）。
        """
        captured_at_ms = int(time.time() * 1000)

        if not self._ensure_mss():
            return ScreenCaptureResult(captured_at_ms=captured_at_ms)

        monitors = self.list_monitors()
        selected, _fallback = self._select_monitor(monitors, monitor_index)

        resolved = self._resolve_region(selected, region)

        # 构造 mss.grab 字典
        grab_dict: dict[str, int] = {
            "left": selected.left,
            "top": selected.top,
            "width": selected.width,
            "height": selected.height,
        }
        if resolved is not None:
            grab_dict["left"] = resolved[0]
            grab_dict["top"] = resolved[1]
            grab_dict["width"] = resolved[2]
            grab_dict["height"] = resolved[3]

        try:
            with _per_monitor_dpi_thread(), _mss.mss() as sct:  # type: ignore[misc]
                sct_img = sct.grab(grab_dict)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception as exc:  # noqa: BLE001 - 后端边界：抓取失败走降级
            logger.warning(
                f"mss 抓取失败（monitor={monitor_index} region={region} grab={grab_dict}）: {type(exc).__name__}: {exc}"
            )
            return ScreenCaptureResult(captured_at_ms=captured_at_ms)

        # 真缩放（等比，按 max_width）
        out_width, out_height = img.width, img.height
        region_out: Optional[List[int]] = None
        if resolved is not None:
            region_out = [
                resolved[0] - selected.left,
                resolved[1] - selected.top,
                resolved[0] - selected.left + resolved[2],
                resolved[1] - selected.top + resolved[3],
            ]
        if max_width is not None and max_width > 0 and img.width > max_width:
            ratio = max_width / img.width
            new_w = max_width
            new_h = max(1, int(round(img.height * ratio)))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            out_width, out_height = new_w, new_h

        # 编码 PNG bytes
        import io  # 顶部已有 PIL；io 留给编码分支就近导入（小工具，避免大块提前入）

        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception as exc:  # noqa: BLE001 - 编码失败走降级
            logger.warning(f"截图 PNG 编码失败: {type(exc).__name__}: {exc}")
            return ScreenCaptureResult(captured_at_ms=captured_at_ms)

        return ScreenCaptureResult(
            image=png_bytes,
            width=out_width,
            height=out_height,
            mime_type="image/png",
            region=region_out,
            captured_at_ms=captured_at_ms,
        )


__all__ = ["MonitorInfo", "MssScreenCapture"]
