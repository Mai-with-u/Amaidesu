"""Pillow 截图后端 —— ``ScreenCapture`` 协议的生产实现（零新增依赖）。

基于 ``PIL.ImageGrab.grab()``：
- 全屏 / 指定区域截图，PNG bytes 输出
- 失败（无显示 / 无权限 / 区域无效）返回空 result（``image=None``），
  由 ``LookAtScreenProvider`` 的降级链兜底（不抛）
- 多屏 / 高帧率需求将来换 mss / dxcam：只替换本文件，协议与工具层零改动

与采集器（collectors/screen）的边界：本类只做"调用即看"的单次快照，
不做变化检测轮询（判别口诀：快照型→同步工具）。
"""

from __future__ import annotations

import io
import time
from typing import List, Optional, Tuple

from PIL import ImageGrab

from src.modules.logging import get_logger
from src.modules.tools.perception.look_at_screen import ScreenCaptureResult

logger = get_logger("PillowImageGrabCapture")


class PillowImageGrabCapture:
    """``ScreenCapture`` 协议的 Pillow 实现。

    Example:
        >>> cap = PillowImageGrabCapture()
        >>> result = cap.capture(region=(0, 0, 800, 600))
        >>> result.image is not None  # 有显示会话时为 PNG bytes
        True
    """

    def capture(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> ScreenCaptureResult:
        """截取屏幕快照。

        Args:
            region: 可选区域 ``(x1, y1, x2, y2)``；None = 全屏（主屏）

        Returns:
            :class:`ScreenCaptureResult`；失败时 ``image=None``（不抛）
        """
        captured_at_ms = int(time.time() * 1000)
        try:
            img = ImageGrab.grab(bbox=region, all_screens=False)
        except Exception as exc:  # noqa: BLE001 - 后端边界：失败交降级链
            logger.warning(f"屏幕截图失败（region={region}）: {type(exc).__name__}: {exc}")
            return ScreenCaptureResult(captured_at_ms=captured_at_ms)

        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception as exc:  # noqa: BLE001 - 编码失败同走降级链
            logger.warning(f"截图 PNG 编码失败: {type(exc).__name__}: {exc}")
            return ScreenCaptureResult(captured_at_ms=captured_at_ms)

        region_list: Optional[List[int]] = [int(v) for v in region] if region else None
        return ScreenCaptureResult(
            image=buf.getvalue(),
            width=int(img.width),
            height=int(img.height),
            mime_type="image/png",
            region=region_list,
            captured_at_ms=captured_at_ms,
        )


__all__ = ["PillowImageGrabCapture"]
