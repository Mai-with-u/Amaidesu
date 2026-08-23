"""
RateLimitInterceptor —— 限流事件拦截器（v2 / Wave 5）

由 ``src/stages/input/pipelines/rate_limit/pipeline.py`` 转换（Pipeline → Interceptor）。

差异：旧版是 ``Pipeline[NormalizedMessage]._process``；新版拦截 ``room.message.*`` /
``input.message.received`` 等事件，按 (event_name + user_id) 键做滑动窗口限流。
- 全局消息频率限制（滑动窗口）
- 用户级消息频率限制（滑动窗口）
- 超限返回 ``None`` 丢弃事件

verbatim 边界：滑动窗口算法、过期清理逻辑 —— 未改动。
仅调整：基类（Pipeline → EventInterceptor）、接口签名（item → event_name/payload/source）、
统计点（self._stats → 仅日志）。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

from src.modules.events.interceptors.base import EventInterceptor
from src.modules.logging import get_logger

# user_id 在 payload 中的查找候选键（兼容多种 payload 形状）
_USER_ID_KEYS = ("user_id", "open_id", "uid", "sender_id")
# text 在 payload 中的查找候选键
_TEXT_KEYS = ("text", "content", "msg", "message")


class RateLimitInterceptor(EventInterceptor):
    """
    限流事件拦截器

    - 全局消息频率限制（每 ``window_size`` 秒内最多 ``global_rate_limit`` 条）
    - 用户级消息频率限制（每 ``window_size`` 秒内最多 ``user_rate_limit`` 条/用户）
    - 超限返回 ``None`` 丢弃事件

    配置参数：
        global_rate_limit (int): 全局上限（默认 100）
        user_rate_limit (int): 每用户上限（默认 10）
        window_size (int): 滑动窗口大小（秒，默认 60）
    """

    def __init__(
        self,
        global_rate_limit: int = 100,
        user_rate_limit: int = 10,
        window_size: int = 60,
    ) -> None:
        self._global_rate_limit = global_rate_limit
        self._user_rate_limit = user_rate_limit
        self._window_size = window_size

        self._global_timestamps: Deque[float] = deque()
        self._user_timestamps: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

        self.logger = get_logger("RateLimitInterceptor")
        self.logger.info(
            f"RateLimitInterceptor 初始化: "
            f"全局={self._global_rate_limit}/{self._window_size}s, "
            f"用户={self._user_rate_limit}/{self._window_size}s"
        )

    @property
    def name(self) -> str:
        return "rate_limit"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        user_id = self._extract_user_id(payload)
        current_time = time.time()

        await self._clean_expired_timestamps(current_time)

        if await self._is_throttled(user_id):
            text_preview = self._extract_text(payload)
            self.logger.info(
                f"消息限流: user_id={user_id}, event={event_name}, "
                f"text_preview='{text_preview[:50]}{'...' if len(text_preview) > 50 else ''}'"
            )
            return None

        await self._record_message(user_id, current_time)
        return payload

    async def _clean_expired_timestamps(self, current_time: float) -> None:
        async with self._lock:
            cutoff_time = current_time - self._window_size

            while self._global_timestamps and self._global_timestamps[0] < cutoff_time:
                self._global_timestamps.popleft()

            for user_id in list(self._user_timestamps.keys()):
                timestamps = self._user_timestamps[user_id]
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()
                if not timestamps:
                    del self._user_timestamps[user_id]

    async def _is_throttled(self, user_id: str) -> bool:
        async with self._lock:
            global_count = len(self._global_timestamps)
            if global_count >= self._global_rate_limit:
                self.logger.warning(
                    f"全局消息限流触发: {global_count}/{self._window_size}s "
                    f">= {self._global_rate_limit}/{self._window_size}s"
                )
                return True

            user_timestamps = self._user_timestamps.get(user_id)
            if user_timestamps and len(user_timestamps) >= self._user_rate_limit:
                self.logger.warning(
                    f"用户 {user_id} 消息限流触发: "
                    f"{len(user_timestamps)}/{self._window_size}s "
                    f">= {self._user_rate_limit}/{self._window_size}s"
                )
                return True

            return False

    async def _record_message(self, user_id: str, current_time: float) -> None:
        async with self._lock:
            self._global_timestamps.append(current_time)
            self._user_timestamps[user_id].append(current_time)

    @staticmethod
    def _extract_user_id(payload: Dict[str, Any]) -> str:
        for key in _USER_ID_KEYS:
            value = payload.get(key)
            if value is not None and value != "":
                return str(value)
        return "unknown_user"

    @staticmethod
    def _extract_text(payload: Dict[str, Any]) -> str:
        for key in _TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
        # 嵌套 user.name 之类（room.message.* payload 的形状）
        user = payload.get("user")
        if isinstance(user, dict):
            name = user.get("name", "")
            if name:
                return f"[{name}]"
        return ""

    async def reset(self) -> None:
        """重置所有计数器（便于测试）"""
        async with self._lock:
            self._global_timestamps.clear()
            self._user_timestamps.clear()
        self.logger.debug("RateLimitInterceptor 已重置")


__all__ = ["RateLimitInterceptor"]
