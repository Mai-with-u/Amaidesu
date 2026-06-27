"""限流管道（Input 阶段）

基于滑动时间窗口算法限制消息发送频率。
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from src.modules.pipeline import Pipeline, pipeline
from src.modules.types.base.normalized_message import NormalizedMessage


@pipeline("rate_limit")
class RateLimitInputPipeline(Pipeline[NormalizedMessage]):
    """限流管道。"""

    priority = 100

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self._global_rate_limit = self.config.get("global_rate_limit", 100)
        self._user_rate_limit = self.config.get("user_rate_limit", 10)
        self._window_size = self.config.get("window_size", 60)

        self._global_timestamps: deque = deque()
        self._user_timestamps: Dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

        self.logger.info(
            f"RateLimitInputPipeline 初始化: "
            f"全局限制={self._global_rate_limit}/分钟, "
            f"用户限制={self._user_rate_limit}/分钟, "
            f"窗口={self._window_size}秒"
        )

    async def _process(self, item: NormalizedMessage) -> Optional[NormalizedMessage]:
        user_id = item.user_id or "unknown_user"
        current_time = time.time()

        await self._clean_expired_timestamps(current_time)

        if await self._is_throttled(user_id):
            self._stats.dropped_count += 1
            self.logger.info(
                f"消息限流: user_id={user_id}, text_preview='{item.text[:50]}{'...' if len(item.text) > 50 else ''}'"
            )
            return None

        await self._record_message(user_id, current_time)
        return item

    async def _clean_expired_timestamps(self, current_time: float) -> None:
        async with self._lock:
            cutoff_time = current_time - self._window_size

            while self._global_timestamps and self._global_timestamps[0] < cutoff_time:
                self._global_timestamps.popleft()

            for user_id, timestamps in list(self._user_timestamps.items()):
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()

                if not timestamps:
                    del self._user_timestamps[user_id]

    async def _is_throttled(self, user_id: str) -> bool:
        async with self._lock:
            global_count = len(self._global_timestamps)
            if global_count >= self._global_rate_limit:
                self.logger.warning(
                    f"全局消息限流触发: 当前速率 {global_count}/{self._window_size}秒 "
                    f"超过限制 {self._global_rate_limit}/{self._window_size}秒"
                )
                return True

            user_timestamps = self._user_timestamps.get(user_id)
            if user_timestamps and len(user_timestamps) >= self._user_rate_limit:
                self.logger.warning(
                    f"用户 {user_id} 消息限流触发: "
                    f"当前速率 {len(user_timestamps)}/{self._window_size}秒 "
                    f"超过限制 {self._user_rate_limit}/{self._window_size}秒"
                )
                return True

            return False

    async def _record_message(self, user_id: str, current_time: float) -> None:
        async with self._lock:
            self._global_timestamps.append(current_time)
            self._user_timestamps[user_id].append(current_time)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update(
            {
                "global_rate_limit": self._global_rate_limit,
                "user_rate_limit": self._user_rate_limit,
                "window_size": self._window_size,
                "current_global_count": len(self._global_timestamps),
                "active_users": len(self._user_timestamps),
            }
        )
        return info

    async def reset(self) -> None:
        async with self._lock:
            self._global_timestamps.clear()
            self._user_timestamps.clear()
        self.reset_stats()
        self.logger.debug("RateLimitInputPipeline 已重置状态")
