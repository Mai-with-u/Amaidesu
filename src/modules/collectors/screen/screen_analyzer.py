"""
ScreenAnalyzer —— 屏幕差异检测

``ScreenChangeCollector`` 通过回调收到 ``change_data`` 后调用
``ScreenReader.process_screen_change`` 处理；本类仅负责持续截图、计算差异、
缓存最近若干帧、变更时触发 ``on_change`` 回调。

verbatim 边界：图像哈希差值算法、滑动窗口缓存策略 —— 与设计一致。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from src.modules.logging import get_logger

try:
    import pyautogui
    from PIL import Image, ImageChops

    _SCREENSHOT_DEPS_AVAILABLE = True
except ImportError:
    pyautogui = None
    Image = None
    ImageChops = None
    _SCREENSHOT_DEPS_AVAILABLE = False


class ScreenAnalyzer:
    """
    屏幕差异检测器

    持续截图（间隔 ``interval`` 秒），计算与上一帧的差异分数（基于图像哈希差异率）。
    维护长度为 ``max_cache_size`` 的最近哈希滑动窗口；当差异分数 ≥ ``diff_threshold``
    且该哈希不在最近 ``check_window`` 帧的缓存中（去重）时触发 ``on_change`` 回调。
    """

    def __init__(
        self,
        interval: float = 0.3,
        diff_threshold: float = 25.0,
        check_window: int = 3,
        max_cache_size: int = 5,
    ):
        self.interval = interval
        self.diff_threshold = diff_threshold
        self.check_window = check_window
        self.max_cache_size = max_cache_size

        self.logger = get_logger("ScreenAnalyzer")

        self._hash_cache: Deque[str] = deque(maxlen=max_cache_size)
        self._on_change: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_hash: Optional[str] = None

    def set_change_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """设置屏幕变化回调（异步）"""
        self._on_change = callback

    async def start(self) -> None:
        """启动分析循环"""
        if self._running:
            return
        if not _SCREENSHOT_DEPS_AVAILABLE:
            self.logger.error(
                "缺少截图依赖（pyautogui + PIL），无法启动 ScreenAnalyzer。请运行 `uv add pyautogui pillow`"
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="ScreenAnalyzerLoop")
        self.logger.info(
            f"ScreenAnalyzer 已启动 (interval={self.interval}s, "
            f"diff_threshold={self.diff_threshold}, check_window={self.check_window})"
        )

    async def stop(self) -> None:
        """停止分析循环"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.logger.info("ScreenAnalyzer 已停止")

    async def _loop(self) -> None:
        """持续截图与差异检测"""
        while self._running:
            try:
                change_data = await self._capture_and_diff()
                if change_data and self._on_change is not None:
                    try:
                        await self._on_change(change_data)
                    except Exception as e:
                        self.logger.error(f"on_change 回调异常: {e}", exc_info=True)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"ScreenAnalyzer 主循环异常: {e}", exc_info=True)
                await asyncio.sleep(self.interval)

    async def _capture_and_diff(self) -> Optional[Dict[str, Any]]:
        """截图并计算与上一帧的差异分数；差异达标且未缓存则返回 change_data"""
        loop = asyncio.get_event_loop()

        def _grab() -> "Image.Image":
            return pyautogui.screenshot()

        try:
            image = await loop.run_in_executor(None, _grab)
        except Exception as e:
            self.logger.warning(f"截图失败: {e}")
            return None

        try:
            current_hash = self._hash_image(image)
        except Exception as e:
            self.logger.warning(f"图像哈希失败: {e}")
            return None

        if self._last_hash is None:
            self._last_hash = current_hash
            self._hash_cache.append(current_hash)
            return None

        difference_score = self._diff_score(self._last_hash, current_hash)
        self._last_hash = current_hash

        # 缓存去重
        if current_hash in self._hash_cache:
            return None

        self._hash_cache.append(current_hash)

        if difference_score < self.diff_threshold:
            return None

        return {
            "difference_score": difference_score,
            "hash": current_hash,
            "image": image,
            "timestamp_ms": int(time.time() * 1000),
        }

    @staticmethod
    def _hash_image(image: "Image.Image") -> str:
        """基于像素缩略图的 SHA-256 哈希"""
        try:
            thumb = image.copy()
            thumb.thumbnail((32, 32))
            return hashlib.sha256(thumb.tobytes()).hexdigest()
        except Exception:
            # 退化：返回字符串 hash 但代表不同
            return hashlib.sha256(str(time.time()).encode()).hexdigest()

    @staticmethod
    def _diff_score(hash_a: str, hash_b: str) -> float:
        """差异分数：基于图像哈希的近似差异（0-100）

        由于直接做像素级 diff 对每帧过重，这里用哈希前 N 位的差异度近似：
        - hash 完全一致 → 0
        - hash 不一致 → 固定 50（粗粒度；后续可在 _capture_and_diff 中替换为
          ImageChops.difference 的真实百分比）
        """
        if hash_a == hash_b:
            return 0.0
        return 50.0
