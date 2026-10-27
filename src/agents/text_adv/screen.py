"""屏幕稳定判定与双层去重指纹。

视觉小说类游戏普遍存在打字机动画——"画面在变"与"文本已稳定"是两件事：
固定延迟要么在动画中途读到半句，要么为慢动画浪费等待。稳定判定以
"连续 N 帧指纹相同"自适应判定画面静止；去重以帧级与识别文本级两层指纹
防止光标闪烁、特效等造成的假变化触发重复读取与重复上报。

指纹只做字节哈希的廉价判断，不读内容、不做像素级 diff、不持久化。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Optional, Protocol, Tuple

from src.modules.logging import get_logger
from src.modules.vision.capture_types import ScreenCaptureResult

logger = get_logger("TextAdvScreen")


class _ScreenCapture(Protocol):
    """稳定判定所需的采集能力（与屏幕线的 ``ScreenCapture`` 协议同形）。

    单独声明是为了让本模块只依赖它真正用到的调用面，测试也可只造最小假件。
    """

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        """截取一次屏幕快照；``image=None`` 表示本次采集失败。"""
        ...


class FrameFingerprint:
    """捕获图像的廉价指纹（图像字节的 SHA-256 十六进制摘要）。

    仅用于"变了没"的等价判断，不承载内容语义；同一图像字节必得同一指纹。
    """

    @staticmethod
    def of(image: bytes) -> str:
        """计算图像字节指纹。"""
        return hashlib.sha256(image).hexdigest()


@dataclass(frozen=True, slots=True)
class StableFrameResult:
    """稳定判定的结果。

    Attributes:
        image: 判定为稳定时返回的帧；超时时为最后一帧。整个窗口内一次
            成功捕获都没有（capture 持续失败）时为空字节串，调用方按
            感知失败路径处理。
        timed_out: 是否因超时而返回（True = 未达成连续相同，调用方决定
            是否降级继续）。
    """

    image: bytes
    timed_out: bool


async def wait_stable(
    capture: _ScreenCapture,
    region: Optional[Tuple[int, int, int, int]],
    monitor_index: int,
    *,
    sample_ms: int = 150,
    consecutive: int = 2,
    timeout_ms: int,
) -> StableFrameResult:
    """轮询采集直到连续 ``consecutive`` 帧指纹相同（画面静止），或超时兜底。

    以 ``sample_ms`` 间隔采样（异步 sleep，不阻塞事件循环）；单次采集失败
    （``image=None``）不计入连续计数、不清空已积累的连续性，仅跳过本轮。
    稳定即返回当前帧且 ``timed_out=False``；到达 ``timeout_ms`` 仍未稳定
    则返回最后一帧且 ``timed_out=True``——超时不抛异常，降级与否由调用方
    决定。

    Args:
        capture: 屏幕采集后端（依赖注入点）。
        region: 可选采集区域 ``(x1, y1, x2, y2)``，相对显示器左上角；
            None = 全屏。语义与采集后端约定一致（通常传整个游戏窗口）。
        monitor_index: 显示器索引。
        sample_ms: 采样间隔（毫秒）。
        consecutive: 判定稳定所需的连续相同帧数。
        timeout_ms: 总超时（毫秒）。

    Returns:
        :class:`StableFrameResult`，同时携带帧与是否超时。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    sample_s = sample_ms / 1000

    last_image = b""
    prev_fingerprint: Optional[str] = None
    streak = 0

    while True:
        snapshot = capture.capture(monitor_index, region)
        if snapshot.image is not None:
            fingerprint = FrameFingerprint.of(snapshot.image)
            streak = streak + 1 if fingerprint == prev_fingerprint else 1
            prev_fingerprint = fingerprint
            last_image = snapshot.image
            if streak >= consecutive:
                return StableFrameResult(image=last_image, timed_out=False)
        else:
            logger.warning(f"稳定判定期间采集失败（monitor={monitor_index} region={region}），跳过本轮")
        if loop.time() >= deadline:
            return StableFrameResult(image=last_image, timed_out=True)
        await asyncio.sleep(sample_s)


def frame_key(image: bytes) -> str:
    """帧级去重指纹：图像字节的哈希摘要。

    用于"同一帧不重复读"——与上一帧指纹相同即视为画面未变化。
    """
    return FrameFingerprint.of(image)


def text_key(text: str) -> str:
    """识别文本级去重指纹：规范化空白后哈希。

    光标闪烁、特效等会造成帧级指纹变化但文本内容不变；对文本先做空白
    规范化（各类空白序列折叠为单个空格、去首尾）再取哈希，可把这类假
    变化判为同一屏，避免重复上报。
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
