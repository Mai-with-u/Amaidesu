"""Windows 进程 DPI 感知设置（须在 Tk 窗口创建前调用）。

## 为什么需要

DPI 感知决定"屏幕坐标"的含义。进程声明为 per-monitor-v2 时，窗口位置、
鼠标屏幕坐标与 Windows 枚举出的显示器矩形同为物理像素；而 system-aware
或 DPI-unaware 的进程在混合 DPI 环境下拿到的坐标会被按系统 DPI 虚拟化，
各显示器矩形被缩放成不相等的口径。

字幕窗按绝对坐标摆放并把用户拖动夹取在显示器范围内：坐标系一旦分叉，
夹取算出的"屏幕右沿"落在主屏边界上，窗口拖到主屏边缘就过不去（多显示器
失效的直接成因）。Tk 8.6.9+ 自行声明 per-monitor-v2，但仅在第一个 Tk 窗口
创建时生效；进程若在此前已被别的组件声明成其他口径，Tk 只能沿用。这里在
启动早期显式声明，把这份口径确定下来，不依赖模块的加载顺序。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

from src.modules.logging import get_logger

logger = get_logger("WindowsDpi")

# Win10 1607+ 用户态 DPI 感知上下文（-4 = PER_MONITOR_AWARE_V2）
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
# Win8.1+ 的进程 DPI 感知枚举（1 = PROCESS_PER_MONITOR_DPI_AWARE）
_PROCESS_PER_MONITOR_DPI_AWARE = 1


def _user32() -> Optional[ctypes.CDLL]:
    """user32 句柄；非 Windows 平台返回 None。"""
    if sys.platform != "win32":
        return None
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _shcore() -> Optional[ctypes.CDLL]:
    """shcore 句柄；平台不符或库缺失时返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.shcore  # type: ignore[attr-defined]
    except (AttributeError, OSError) as e:
        logger.debug(f"shcore 不可用，跳过旧接口回退: {e}")
        return None


def setup_process_dpi_awareness() -> bool:
    """声明进程为 per-monitor-v2 DPI 感知，返回是否声明成功（或本就如此）。

    两条路径依次尝试：Win10 1607+ 的上下文接口优先，缺失或失败时回退到
    Win8.1+ 的进程感知枚举。感知已由其他机制确定（清单声明等）时接口会
    失败——此时进程口径已固定，不视为错误，按失败记录 debug 供排查。
    """
    user32 = _user32()
    if user32 is not None:
        set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.restype = ctypes.c_bool
            set_context.argtypes = [ctypes.c_void_p]
            if set_context(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
                logger.info("进程 DPI 感知：已设为 per-monitor-v2")
                return True

    shcore = _shcore()
    set_awareness = getattr(shcore, "SetProcessDpiAwareness", None) if shcore is not None else None
    if set_awareness is not None:
        try:
            # 返回 S_OK / E_ACCESSDENIED 等 HRESULT；负值表示失败
            hr = set_awareness(_PROCESS_PER_MONITOR_DPI_AWARE)
        except OSError as e:
            logger.debug(f"SetProcessDpiAwareness 调用失败: {e}")
        else:
            if hr >= 0:
                logger.info("进程 DPI 感知：已回退设为 per-monitor（旧接口）")
                return True
            logger.debug(f"进程 DPI 感知设置被拒绝（HRESULT={hr:#x}），沿用已有口径")

    logger.debug("进程 DPI 感知未能显式设置，沿用系统已有口径")
    return False


__all__ = ["setup_process_dpi_awareness"]
