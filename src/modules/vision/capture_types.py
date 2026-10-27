"""屏幕采集/文本识别的共享协议与数据类型。

采集后端（``mss_capture``）、工具实现（``look_at_screen``）与文本识别实现
（``vlm_reader``）共同依赖的 Protocol 与返回值类型集中在此，保证包内依赖
方向单向：后端实现不反向依赖工具模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple


@dataclass(slots=True)
class ScreenCaptureResult:
    """一次屏幕采集的返回值（图像 + 元数据）。

    Attributes:
        image: 图像数据（bytes 形态 PNG / JPEG；None = 后端不可用）
        width: 像素宽（image=None 时可为 0）
        height: 像素高
        mime_type: 图像 MIME（如 ``"image/png"``；image=None 时为空）
        region: 实际采集区域 ``[x1, y1, x2, y2]``；None 表示全屏
        captured_at_ms: 采集时刻（Unix 毫秒）
        monitor_index: 实际使用的显示器索引（非法回退后会与入参不同）
    """

    image: Optional[bytes] = None
    width: int = 0
    height: int = 0
    mime_type: str = ""
    region: Optional[List[int]] = None
    captured_at_ms: int = 0
    monitor_index: int = 0


class ScreenCapture(Protocol):
    """屏幕采集后端协议（依赖注入点）。

    生产实现为 ``MssScreenCapture``（基于 mss 库，支持多显示器 / 区域相对换算
    / 越界 clamp / 真缩放）；测试用 Fake 替身（tests/modules/vision/fakes.py）。后端缺失或抓取
    失败时 ``image=None``，由 ``LookAtScreenProvider`` 走降级路径。
    """

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """截取指定显示器（+ 可选区域）的快照。

        Args:
            monitor_index: 显示器索引（1..N 物理显示器）；非法 → 后端自行
                决定降级（``MssScreenCapture`` 回退到首个物理显示器 + warning）。
            region: 可选区域 ``(x1, y1, x2, y2)``，相对所选显示器左上角；
                None = 全屏。
            max_width: 非 None 且图宽 > ``max_width`` 时做等比缩放；
                None 或 0 = 不缩放。

        Returns:
            :class:`ScreenCaptureResult`；``image=None`` 表示后端不可用或抓取失败。
        """
        ...


class TextReader(Protocol):
    """图像→文本 协议（OCR / VLM 均可实现，异步）。

    异步契约是 VLM/OCR 等 I/O 调用的硬要求，避免阻塞事件循环。失败 / 超时
    一律返回 ``""``（由 ``LookAtScreenProvider`` 组装 ``error`` 字段）。
    """

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        """从图像提取文本（OCR / VLM 描述）。

        Args:
            image_bytes: 图像字节（PNG/JPEG 等）
            mime_type: 图像 MIME
            question: 调用方对本次识别的提问（如"屏幕上显示什么"）；
                None = 实现自行决定使用默认提示

        Returns:
            提取的文本（空串表示无可读文本或识别失败/超时/异常）
        """
        ...


__all__ = [
    "ScreenCapture",
    "ScreenCaptureResult",
    "TextReader",
]
