"""ScreenSession —— 观察会话：屏幕感知链采集语义的单点。

"text_adv 怎么安全看屏幕"此前分散在 agent（观察循环）与 tools（工具面）
两处各持一份；本模块把采集语义收拢为一份：窗口查找/聚焦/前台判定、采集区域
换算、稳定帧等待、读屏、廉价帧采样。

切分原则是"会话管采集、调用方管处置"：
- 会话返回结构化的采集结果（成功带数据 / 失败带原因），不决定任何循环的
  生死；``read_screen`` 透传读屏后端异常、``sample_frame`` 透传采集异常，
  失败后做什么（观察循环停循环发 error / 工具回执错误）由调用方定
- 例外是 ``stable_frame``：稳定判定失败在两个调用方都按"感知失败"降级
  （返回 ``None`` 继续走），因此降级语义本身就属于采集语义，收进会话

本模块不持键鼠注入（动作出口归工具面）、不持游戏内容状态（归 Agent）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Tuple

from src.modules.logging import get_logger
from src.modules.vision.capture_types import ScreenCaptureResult

from .config import TextAdvConfig
from .screen import StableFrameResult, wait_stable
from .vlm import ScreenReading, VisionReader
from .window import WindowBackend, WindowInfo

logger = get_logger("TextAdvScreenSession")


class FrameCapture(Protocol):
    """帧采集能力（与屏幕线 ``ScreenCapture`` 协议同形，稳定判定与廉价帧检查共用）。"""

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """截取一次屏幕快照；``image=None`` 表示本次采集失败。"""
        ...


# ---------------------------------------------------------------------------
# 监视器几何（坐标反算的显示器原点来源；可注入替换以便测试）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonitorGeometry:
    """单台显示器的虚拟桌面几何（物理像素）。

    Attributes:
        left: 显示器左上角在虚拟桌面坐标系中的绝对 X
        top: 显示器左上角在虚拟桌面坐标系中的绝对 Y
        width: 显示器宽度（物理像素）
        height: 显示器高度（物理像素）
    """

    left: int
    top: int
    width: int
    height: int


MonitorResolver = Callable[[int], Optional[MonitorGeometry]]


def query_monitor_geometry(monitor_index: int) -> Optional[MonitorGeometry]:
    """按显示器编号查询虚拟桌面几何（mss 的 monitors 表，[0] 为全体桌面）。

    查询失败（库缺失 / 编号越界 / 异常）返回 ``None``，调用方据此判定
    坐标链路不可用并走拒绝路径。
    """
    try:
        # 可选重型依赖延迟加载：mss 缺失或查询失败都走下方降级路径（返回 None），
        # 调用方按坐标链路不可用拒绝
        import mss

        with mss.mss() as sct:
            monitors = sct.monitors
            idx = monitor_index + 1
            if idx >= len(monitors):
                logger.error(f"监视器原点查询失败：编号 {monitor_index} 越界（共 {len(monitors) - 1} 台）")
                return None
            mon = monitors[idx]
            return MonitorGeometry(
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
            )
    except Exception as exc:  # noqa: BLE001 - 坐标链路不可用按拒绝处理，不上抛
        logger.exception(f"监视器原点查询失败（monitor={monitor_index}）: {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# 采集结果的数据形状
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """窗口查找的结构化结果。

    Attributes:
        win: 找到的窗口快照；未找到为 ``None``
        reason: 未找到时的可读原因；找到时为 ``None``
    """

    win: Optional[WindowInfo]
    reason: Optional[str]

    @property
    def ok(self) -> bool:
        """查找是否成功（win 非 None 且无原因）。"""
        return self.win is not None and self.reason is None


# ---------------------------------------------------------------------------
# 会话本体
# ---------------------------------------------------------------------------


class ScreenSession:
    """一个文字冒险游戏窗口的观察会话（采集语义单点）。

    持有读屏/帧采集/窗口三个注入后端与运行配置，向调用方（观察循环与
    工具面）提供同一套"看屏幕"的原语；自身无跨调用状态，不做任何处置
    决策（停循环、回执错误都归调用方）。
    """

    def __init__(
        self,
        config: TextAdvConfig,
        *,
        window_backend: WindowBackend,
        capture: FrameCapture,
        vision_reader: VisionReader,
    ) -> None:
        """构造注入全部感知依赖（与 Agent 构造参数一一对应）。"""
        self._config = config
        self._window_backend = window_backend
        self._capture = capture
        self._vision_reader = vision_reader

    # ==================================================================
    # 窗口护栏原语（处置归调用方：循环停循环、工具回执错误）
    # ==================================================================

    def find_window(self) -> WindowOutcome:
        """按配置的标题关键字查找游戏窗口。"""
        keyword = self._config.game_window_title_keyword
        win = self._window_backend.find(keyword)
        if win is None:
            return WindowOutcome(win=None, reason=f"未找到游戏窗口（标题关键词={keyword!r}）")
        return WindowOutcome(win=win, reason=None)

    def focus_window(self, win: WindowInfo) -> Optional[str]:
        """真夺焦（advance 是显式命令，允许夺焦）；失败返回可读原因，成功返回 ``None``。"""
        if not self._window_backend.focus(win):
            return f"游戏窗口夺焦失败（title={win.title!r}）"
        return None

    def check_watchable(self, win: WindowInfo) -> Optional[str]:
        """检查已持有的窗口当前是否还能看（前台焦点 + 几何可得）。

        Returns:
            ``None`` 表示通过；否则为不可看的原因（循环据此停循环）。
        """
        if not self._window_backend.is_foreground(win):
            return "游戏窗口失去前台焦点"
        if self._window_backend.rect(win) is None:
            return "窗口几何不可得（窗口可能已关闭）"
        return None

    # ==================================================================
    # 区域换算（配置区域是唯一的区域事实源，换算只此一份）
    # ==================================================================

    def capture_region(self) -> Optional[Tuple[int, int, int, int]]:
        """配置区域 [x, y, w, h] → 采集契约的 (x1, y1, x2, y2)；未配置返回 None。"""
        raw = self._config.region
        if raw is None or len(raw) != 4:
            return None
        x, y, w, h = raw
        return (x, y, x + w, y + h)

    def coords_region(self, geometry: MonitorGeometry) -> Tuple[int, int, int, int]:
        """坐标反算用的 region (left, top, width, height)；未配置时取整台显示器。"""
        raw = self._config.region
        if raw is None or len(raw) != 4:
            return (0, 0, geometry.width, geometry.height)
        return (raw[0], raw[1], raw[2], raw[3])

    # ==================================================================
    # 采集
    # ==================================================================

    async def stable_frame(self) -> Optional[StableFrameResult]:
        """等待画面稳定；采集异常按感知失败降级（返回 ``None``，不外抛）。

        两个调用方（观察循环 / 工具面）对稳定判定失败的处置一致——降级
        继续，因此该降级语义收在会话内；返回结果的进一步处置归调用方。
        """
        cfg = self._config
        try:
            return await wait_stable(
                self._capture,
                self.capture_region(),
                cfg.monitor_index,
                sample_ms=cfg.stability_sample_ms,
                consecutive=cfg.stability_consecutive,
                timeout_ms=cfg.stability_timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 采集异常是感知失败，不是调用方异常
            logger.warning(f"稳定判定期间采集异常: {type(exc).__name__}: {exc}")
            return None

    async def read_screen(self) -> ScreenReading:
        """读一次当前屏（带选项坐标）；读屏后端异常原样上抛，处置归调用方。

        读屏失败在两个调用方的处置不同——工具面降级为错误回执，观察循环
        按循环整体死亡处理——故会话不吞异常、不代做决定。
        """
        return await self._vision_reader.read_screen(want_options=True)

    def sample_frame(self, region: Optional[Tuple[int, int, int, int]]) -> Optional[bytes]:
        """廉价单帧采样（降频段的帧级去重检查用，不等待稳定）。

        采集异常原样上抛（调用方的整体异常路径定生死）；快照本身失败
        （``image=None``）返回 ``None``。
        """
        snapshot = self._capture.capture(self._config.monitor_index, region)
        return snapshot.image if snapshot is not None else None


__all__ = [
    "FrameCapture",
    "MonitorGeometry",
    "MonitorResolver",
    "ScreenSession",
    "WindowOutcome",
    "query_monitor_geometry",
]
