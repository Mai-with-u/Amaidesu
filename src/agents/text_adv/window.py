"""窗口后端——真夺焦、几何读取、失焦与位移检测。

定位：
- 窗口操作是**被动能力**（被调才干活），由 :class:`WindowBackend` Protocol
  统一注入；生产实现为 :class:`PyGetWindowBackend`（基于 pygetwindow），
  测试用 :class:`FakeWindowBackend`。
- 夺焦取代"点画面夺焦"——视觉小说里点击画面会误推进一屏。
- 部署约定窗口化运行：独占全屏下窗口 API 取不到窗口，所有查找都会落空。

失败语义（不抛、不静默）：
- 每条失败路径都返回可判定的 ``None`` / ``False`` 并记日志，由调用方
  决定降级与报错方式。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Tuple

import pygetwindow

from src.modules.logging import get_logger

logger = get_logger("PyGetWindowBackend")

# 窗口几何快照：(left, top, width, height)，显示器相对物理像素。
WindowRect = Tuple[int, int, int, int]


@dataclass(slots=True)
class WindowInfo:
    """一次窗口查找的结果快照。

    Attributes:
        title: 窗口标题（匹配到的实际标题）
        left: 窗口左上角 X（显示器相对物理像素）
        top: 窗口左上角 Y
        width: 窗口宽
        height: 窗口高
        handle: 后端私有的窗口句柄（生产为 pygetwindow 窗口对象，
            测试假件可为任意标记）；后续操作凭它路由回真实窗口
    """

    title: str
    left: int
    top: int
    width: int
    height: int
    handle: Any = None


class WindowBackend(Protocol):
    """窗口后端协议（依赖注入点）。"""

    def find(self, title_keyword: str) -> Optional[WindowInfo]:
        """按标题关键字查找窗口；找不到返回 ``None``，不抛。"""
        ...

    def focus(self, win: WindowInfo) -> bool:
        """夺焦；失败返回 ``False``。"""
        ...

    def rect(self, win: WindowInfo) -> Optional[WindowRect]:
        """读取窗口当前几何 ``(left, top, width, height)``；失败返回 ``None``。"""
        ...

    def is_foreground(self, win: WindowInfo) -> bool:
        """窗口是否处于前台焦点；判定失败返回 ``False``。"""
        ...

    def moved_since(self, win: WindowInfo, snapshot: WindowRect) -> bool:
        """窗口几何相对快照是否发生变化（含尺寸变化）；读取失败返回 ``False``。"""
        ...


class PyGetWindowBackend:
    """基于 pygetwindow 的窗口后端实现（Windows）。"""

    def find(self, title_keyword: str) -> Optional[WindowInfo]:
        matches = pygetwindow.getWindowsWithTitle(title_keyword)
        if not matches:
            return None
        target = matches[0]
        return WindowInfo(
            title=target.title,
            left=target.left,
            top=target.top,
            width=target.width,
            height=target.height,
            handle=target,
        )

    def focus(self, win: WindowInfo) -> bool:
        target = self._resolve(win)
        if target is None:
            logger.warning(f"夺焦失败：窗口句柄无效，title={win.title!r}")
            return False
        try:
            target.activate()
            return True
        except Exception:
            logger.exception(f"夺焦失败：activate 抛出异常，title={win.title!r}")
            return False

    def rect(self, win: WindowInfo) -> Optional[WindowRect]:
        target = self._resolve(win)
        if target is None:
            logger.warning(f"读取几何失败：窗口句柄无效，title={win.title!r}")
            return None
        try:
            return (target.left, target.top, target.width, target.height)
        except Exception:
            logger.exception(f"读取几何失败：窗口可能已关闭，title={win.title!r}")
            return None

    def is_foreground(self, win: WindowInfo) -> bool:
        target = self._resolve(win)
        if target is None:
            logger.warning(f"前台判定失败：窗口句柄无效，title={win.title!r}")
            return False
        foreground_handle = self._get_foreground_handle()
        if foreground_handle is None:
            logger.warning("前台判定失败：当前平台不支持前台窗口查询")
            return False
        return target.handle == foreground_handle

    def moved_since(self, win: WindowInfo, snapshot: WindowRect) -> bool:
        current = self.rect(win)
        if current is None:
            return False
        return current != snapshot

    def _resolve(self, win: WindowInfo) -> Optional[Any]:
        """取回 WindowInfo 背后的真实窗口对象；形态不符返回 None。"""
        target = win.handle
        if target is None or not hasattr(target, "activate"):
            return None
        return target

    @staticmethod
    def _get_foreground_handle() -> Optional[Any]:
        """取当前前台窗口的系统句柄；非 Windows 平台返回 None。"""
        if not hasattr(ctypes, "windll"):
            return None
        return ctypes.windll.user32.GetForegroundWindow()


class FakeWindowBackend:
    """测试用窗口后端：预置查找结果 / 夺焦结果 / 前台状态 / 几何矩形。

    Attributes:
        find_calls: 收到的查找关键字序列（保序，供断言）
    """

    def __init__(
        self,
        *,
        found: bool = True,
        rect: WindowRect = (0, 0, 1280, 720),
        focus_ok: bool = True,
        foreground: bool = True,
    ) -> None:
        self._found = found
        self._rect = rect
        self._focus_ok = focus_ok
        self._foreground = foreground
        self.find_calls: list[str] = []

    def set_rect(self, rect: WindowRect) -> None:
        """注入新的窗口几何（模拟窗口被移动/缩放）。"""
        self._rect = rect

    def set_foreground(self, foreground: bool) -> None:
        """注入前台焦点状态。"""
        self._foreground = foreground

    def find(self, title_keyword: str) -> Optional[WindowInfo]:
        self.find_calls.append(title_keyword)
        if not self._found:
            return None
        return WindowInfo(
            title=title_keyword,
            left=self._rect[0],
            top=self._rect[1],
            width=self._rect[2],
            height=self._rect[3],
            handle="fake-window",
        )

    def focus(self, win: WindowInfo) -> bool:
        if win.handle is None:
            logger.warning(f"FakeWindowBackend 夺焦失败：handle 为空，title={win.title!r}")
            return False
        return self._focus_ok

    def rect(self, win: WindowInfo) -> Optional[WindowRect]:
        return self._rect

    def is_foreground(self, win: WindowInfo) -> bool:
        return self._foreground

    def moved_since(self, win: WindowInfo, snapshot: WindowRect) -> bool:
        return self._rect != snapshot
