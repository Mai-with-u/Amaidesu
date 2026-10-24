"""字幕窗口几何学：多显示器枚举、位置夹取、缩放口径解析的纯函数。

几何数学与 Tk 无关：参数只收数字与 ``(left, top, right, bottom)`` 矩形原语，
不收 Tk 对象；Tk 读数（winfo 系列）与 geometry 下发留在
``tk_gui_service`` 的绑定层。位置口径统一为物理像素，与 Tk 的窗口位置
参数（``geometry`` 的 ``+x+y``、事件里的 ``x_root``）同口径。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, List, Optional, Tuple

from src.modules.logging import get_logger

logger = get_logger("WindowGeometry")

# 显示器矩形 (left, top, right, bottom)，物理像素
MonitorRect = Tuple[int, int, int, int]


class _MonitorRectStruct(ctypes.Structure):
    """``MONITORINFO`` 里的显示器矩形（物理像素）。"""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    """``GetMonitorInfoW`` 的出参结构；``rcMonitor`` 为显示器整体矩形。

    取整体矩形而非 ``rcWork``：隐藏任务栏的区域仍属显示器，字幕窗停在那里
    依然可见可抓，按工作区夹取会平白多出一条够不着的带。
    """

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _MonitorRectStruct),
        ("rcWork", _MonitorRectStruct),
        ("dwFlags", ctypes.c_ulong),
    ]


def virtual_screen_bounds() -> Optional[Tuple[int, int, int, int]]:
    """Windows 虚拟桌面范围 (left, top, right, bottom)；取不到返回 None。

    口径是物理像素，前提是进程为 per-monitor DPI 感知，见
    ``src.modules.utils.windows_dpi``。
    """
    try:
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception as e:
        # 非 Windows 或 ctypes 不可用时退回 Tk 报告的屏幕尺寸，不留异常路径
        logger.debug(f"读取虚拟桌面范围失败: {e}")
    return None


def enumerate_monitors() -> Optional[List[MonitorRect]]:
    """枚举显示器矩形 (left, top, right, bottom)，物理像素；失败返回 None。

    逐台枚举而不是只看虚拟桌面包围盒：显示器拼不成矩形时（L 形、上下错位），
    包围盒里存在没有任何显示器覆盖的空洞，把窗口夹取到包围盒内会把它放进
    空洞——窗口既看不见也点不到，而无边框窗没有任务栏入口，等于丢失。
    """
    if sys.platform != "win32":
        return None
    rects: List[MonitorRect] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_MonitorRectStruct),
        ctypes.c_double,
    )

    def _collect(hmonitor: Any, _hdc: Any, _rect: Any, _data: Any) -> int:
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(hmonitor), ctypes.byref(info)):
            mon = info.rcMonitor
            if mon.right > mon.left and mon.bottom > mon.top:
                rects.append((mon.left, mon.top, mon.right, mon.bottom))
        return 1

    try:
        if not ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(_collect), 0):
            logger.debug("EnumDisplayMonitors 未枚举到显示器")
    except Exception as e:
        logger.debug(f"枚举显示器矩形失败: {e}")
        return None
    return rects or None


def parse_requested_size(geometry_string: str) -> Tuple[int, int]:
    """解析 Tk geometry 串中的宽高（逻辑单位）；无尺寸段时返回 (0, 0)。"""
    size = geometry_string.partition("+")[0]
    width_str, _, height_str = size.partition("x")
    width = int(width_str) if width_str.isdigit() else 0
    height = int(height_str) if height_str.isdigit() else 0
    return width, height


def keep_visible_px(size_px: int) -> int:
    """窗口在某个方向上至少要留在显示器内的长度。

    取窗口在该方向尺寸的一半：窗口被拖到显示器边缘时仍有一半可见，标题与
    字幕都还能看见、也点得到；窗口比显示器还大时这条下限按显示器尺寸收窄，
    保证窗口不会因为"要求可见一半"而被完全推出屏外。
    """
    return max(size_px // 2, 40)


def clamp_top_to_monitor(top: int, height_px: int, rect: MonitorRect) -> int:
    """把顶边夹到给定显示器的上下沿内（横向不动，由拖动夹取负责）。

    高度自适应只改高度与顶边，横向位置保持原样；这里也只回收纵向越界——
    锚点贴在显示器下沿时内容变高会把顶边算到屏外，窗口高于显示器时则优先
    保住顶边可见，否则用户再也点不到它。
    """
    _, mon_top, _, mon_bottom = rect
    if height_px > mon_bottom - mon_top:
        return mon_top
    return min(max(top, mon_top), mon_bottom - height_px)


def nearest_monitor(x: int, y: int, rects: List[MonitorRect]) -> MonitorRect:
    """离给定点最近的显示器矩形：到各显示器矩形的距离取最小。

    点到矩形的距离为两轴方向"超出矩形范围"的分量平方和；点在矩形内时
    距离为 0（正常情况下走不到这里，作为兜底语义保留）。``rects`` 为空
    属调用方契约破坏，抛 ``ValueError``。
    """
    if not rects:
        raise ValueError("nearest_monitor 要求 rects 非空")
    best_rect: Optional[MonitorRect] = None
    best_distance = 0
    for rect in rects:
        left, top, right, bottom = rect
        dx = max(left - x, 0, x - right)
        dy = max(top - y, 0, y - bottom)
        distance = dx * dx + dy * dy
        if best_rect is None or distance < best_distance:
            best_rect, best_distance = rect, distance
    return best_rect


def monitor_under_point(x: int, y: int, rects: List[MonitorRect]) -> MonitorRect:
    """给定点所在（或离该点最近）的显示器矩形；``rects`` 非空。"""
    for rect in rects:
        left, top, right, bottom = rect
        if left <= x < right and top <= y < bottom:
            return rect
    return nearest_monitor(x, y, rects)


def clamp_into_rect(
    rect: MonitorRect,
    x: int,
    y: int,
    win_w: int,
    win_h: int,
) -> Tuple[int, int]:
    """把窗口位置夹进单台显示器：横向整体可见、纵向底边不出下沿。

    位置是窗口左上角，右探出的部分按**窗口实际尺寸**回收：跨显示器拖动时
    窗口管理器会按目标屏幕的 DPI 重排窗口，尺寸与拖动开始时不同，按配置
    尺寸夹取会留下"程序以为在屏内、实际探出屏外"的偏差。底边同样不越过
    下沿；顶边允许探出上沿——内容变高时窗口只向上长，把整条窗口往下拽会
    让字幕条离开用户放的位置。窗口高/宽大于显示器时（装不下）改为保住
    顶边与至少一半可见，否则用户再也点不到它。
    """
    mon_left, mon_top, mon_right, mon_bottom = rect
    mon_width = mon_right - mon_left
    mon_height = mon_bottom - mon_top
    win_w = max(win_w, 1)
    win_h = max(win_h, 1)
    keep_x = keep_visible_px(win_w)
    keep_y = keep_visible_px(win_h)
    # 横向：放得下就整体可见；放不下则至少留 keep 像素在显示器内
    x_min = mon_left if win_w <= mon_width else mon_right - keep_x
    x_max = mon_right - win_w if win_w <= mon_width else mon_left - win_w + keep_x
    # 纵向：底边锚定是"字幕条停在哪"的事实源——窗口放得下时保证底边落在显示器
    # 内，顶边可以探出上沿（内容变高只向上长，不把整条窗口往下拽）；窗口高于
    # 显示器时才反过来保住顶边可见，否则用户点不到它
    y_min = mon_top if win_h <= mon_height else mon_bottom - keep_y
    y_max = mon_bottom - win_h if win_h <= mon_height else mon_top
    return min(max(x, x_min), x_max), min(max(y, y_min), y_max)


def clamp_position(
    x: int,
    y: int,
    pointer_x: int,
    pointer_y: int,
    rects: List[MonitorRect],
    win_w: int,
    win_h: int,
) -> Tuple[int, int]:
    """把窗口位置夹到指针所在显示器内（横向不出屏、底边不出下沿）。

    窗口一旦被拖到显示器外就再也抓不回来，而字幕窗是无边框透明窗、没有
    任务栏入口；位置改动一律过这道闸。夹取按指针位置选显示器，而不是落点
    所在的那台：拖到显示器边缘时落点可能已经越过边界（甚至落进多显示器
    拼不成矩形时的空洞），按落点选会让窗口在相邻显示器间跳来跳去。指针与
    落点都没落在任何显示器上时，夹到离落点最近的那台——按虚拟桌面包围盒
    夹取可能把窗口放进空洞，那和无显示器覆盖是一回事。
    """
    # 优先指针所在的那台，其次落点所在的那台
    for px, py in ((pointer_x, pointer_y), (x, y)):
        for rect in rects:
            mon_left, mon_top, mon_right, mon_bottom = rect
            if mon_left <= px < mon_right and mon_top <= py < mon_bottom:
                return clamp_into_rect(rect, x, y, win_w, win_h)
    return clamp_into_rect(nearest_monitor(x, y, rects), x, y, win_w, win_h)


def primary_monitor_rect(rects: List[MonitorRect]) -> Optional[MonitorRect]:
    """主显示器矩形（虚拟桌面原点所在的那台）；没有显示器包含原点时返回 None。"""
    for rect in rects:
        left, top, right, bottom = rect
        if left <= 0 < right and top <= 0 < bottom:
            return rect
    return None
