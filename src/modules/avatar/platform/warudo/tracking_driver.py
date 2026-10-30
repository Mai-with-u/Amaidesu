"""Warudo 面捕驱动：每帧一个 iFacialMocap 数据包发给 Warudo 的面捕接收器，风格化标记走蓝图通道

脸、眼、头都在面捕数据里：Warudo 的 iFacialMocap 接收器按它自己的面捕流程处理，模型有
ARKit 形状（完美同步）就照名字写，没有就按 Warudo 面捕设置里选的映射（如 VRM）换算——
模型相关的对应关系留在 Warudo 里调，和真人用 iPhone 面捕时一样。

面捕数据里没有风格化标记（脸红、眼泪……）。登记了对应形状的标记，值变化时经 WebSocket
蓝图通道发 ``marker_<名字>``（0~1，按 0.05 取整、同一个标记至少隔 100 毫秒才再发，归零和
满值立刻发），由生成的蓝图写到模型形状上。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

from src.modules.avatar.tracking.channels import MARKER_PREFIX, Frame
from src.modules.utils.time_utils import now_ms as wall_now_ms

from .ifacialmocap import encode_frame

# 风格化标记经蓝图通道发送：取整步长与同一标记的最短发送间隔
MARKER_STEP = 0.05
MARKER_MIN_GAP_MS = 100


def marker_action(marker: str) -> str:
    """风格化标记在蓝图通道里的动作名（蓝图生成与驱动共用）。"""
    return f"marker_{marker}"


class WarudoTrackingDriver:
    """Warudo 面捕驱动（满足面捕合成器的驱动契约）。"""

    name = "warudo"

    def __init__(
        self,
        *,
        markers: Optional[Mapping[str, str]] = None,
        send_marker: Optional[Callable[[str, float], Awaitable[bool]]] = None,
        clock: Callable[[], int] = wall_now_ms,
    ) -> None:
        """Args:
        markers: 登记了模型形状的风格化标记（标记名 → 形状名；这里只用到标记名）。
        send_marker: 经蓝图通道发一个标记值，返回是否发出去了（蓝图通道没连上时为 False，下一帧再试）。
        """
        self._markers = tuple(markers or ())
        self._send_marker = send_marker
        self._clock = clock
        self._transport: Any = None
        # 每个标记最近一次发出去的值与时刻
        self._sent: Dict[str, Tuple[float, int]] = {}

    def attach_transport(self, transport: Any) -> None:
        """挂上 UDP 发送端（需有 ``sendto(bytes)`` 与 ``is_closing()``）；None = 摘下。"""
        self._transport = transport
        if transport is None:
            self._sent.clear()

    def ready(self) -> bool:
        # UDP 没有连接：发送端开着就发，Warudo 没开时包落空，不影响别的
        return self._transport is not None and not self._transport.is_closing()

    def marker_updates(self, frame: Frame) -> Dict[str, float]:
        """这一帧该经蓝图通道发的标记：标记名 → 取整后的值（只列该发的，不改状态）。"""
        now = self._clock()
        updates: Dict[str, float] = {}
        for marker in self._markers:
            value = round(
                round(min(1.0, max(0.0, frame.get(MARKER_PREFIX + marker, 0.0))) / MARKER_STEP) * MARKER_STEP, 2
            )
            last = self._sent.get(marker)
            if last is not None and last[0] == value:
                continue
            if last is not None and value not in (0.0, 1.0) and now - last[1] < MARKER_MIN_GAP_MS:
                continue
            updates[marker] = value
        return updates

    async def apply(self, frame: Frame) -> None:
        """这一帧编成一个面捕数据包发出（发完就走），变了的标记再经蓝图通道补发。"""
        self._transport.sendto(encode_frame(frame))
        if self._send_marker is None:
            return
        for marker, value in self.marker_updates(frame).items():
            if await self._send_marker(marker_action(marker), value):
                self._sent[marker] = (value, self._clock())


__all__ = ["MARKER_MIN_GAP_MS", "MARKER_STEP", "WarudoTrackingDriver", "marker_action"]
