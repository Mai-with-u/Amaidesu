"""
限流管道（TextPipeline 版本）

基于滑动时间窗口算法限制消息发送频率。
这是 ThrottlePipeline 的现代化版本，使用 TextPipeline 接口。

3域架构中的位置：
- InputDomain (Input Domain): 在 RawData → NormalizedMessage 转换中处理文本
- 在 InputDomain.normalize() 方法中调用
- 用于限制输入消息频率

功能：
1. 全局消息频率限制 - 控制整个系统每分钟处理的消息总量
2. 用户级别频率限制 - 控制单个用户每分钟可发送的消息数量

设计变更（相比旧版 ThrottlePipeline）：
- 使用 TextPipeline 接口（处理 text + metadata）
- 从 metadata 中提取 user_id
- 支持错误处理策略和超时控制
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from src.domains.input.pipelines.manager import TextPipelineBase


class RateLimitTextPipeline(TextPipelineBase):
    """
    限流管道（TextPipeline 版本）

    基于滑动时间窗口算法限制消息发送频率。
    """

    priority = 100  # 较高优先级，尽早过滤

    def __init__(self, config: Dict[str, Any]):
        """
        初始化限流管道

        Args:
            config: 配置字典，支持以下键:
                global_rate_limit (int): 全局每分钟最大消息数量 (默认: 100)
                user_rate_limit (int): 每个用户每分钟最大消息数量 (默认: 10)
                window_size (int): 滑动窗口大小（秒）(默认: 60)
                error_handling (str): 错误处理策略 (默认: "continue")
                timeout_seconds (float): 超时时间（秒）(默认: 5.0)
        """
        super().__init__(config)

        self._global_rate_limit = self.config.get("global_rate_limit", 100)
        self._user_rate_limit = self.config.get("user_rate_limit", 10)
        self._window_size = self.config.get("window_size", 60)

        # 存储时间戳的数据结构
        self._global_timestamps: deque = deque()
        self._user_timestamps: Dict[str, deque] = defaultdict(deque)

        # 并发控制
        self._lock = asyncio.Lock()

        self.logger.info(
            f"RateLimitTextPipeline 初始化: "
            f"全局限制={self._global_rate_limit}/分钟, "
            f"用户限制={self._user_rate_limit}/分钟, "
            f"窗口={self._window_size}秒"
        )

    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        处理文本，根据限流规则决定是否允许继续传递

        Args:
            text: 待处理的文本
            metadata: 元数据（需要 user_id）

        Returns:
            原始文本（允许通过）或 None（限流丢弃）
        """
        user_id = metadata.get("user_id", "unknown_user")
        current_time = time.time()

        # 清理过期记录
        await self._clean_expired_timestamps(current_time)

        # 检查是否应该限流
        if await self._is_throttled(user_id):
            self._stats.dropped_count += 1
            self.logger.info(
                f"消息限流: user_id={user_id}, text_preview='{text[:50]}{'...' if len(text) > 50 else ''}'"
            )
            return None  # 丢弃该消息

        # 记录通过的消息
        await self._record_message(user_id, current_time)

        # 返回原始文本，允许继续处理
        return text

    async def _clean_expired_timestamps(self, current_time: float) -> None:
        """
        清理过期的时间戳记录

        Args:
            current_time: 当前时间戳
        """
        async with self._lock:
            cutoff_time = current_time - self._window_size

            # 清理全局队列中的过期时间戳
            while self._global_timestamps and self._global_timestamps[0] < cutoff_time:
                self._global_timestamps.popleft()

            # 清理各用户队列中的过期时间戳
            for user_id, timestamps in list(self._user_timestamps.items()):
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()

                # 优化内存: 如果用户队列为空，则从字典中移除
                if not timestamps:
                    del self._user_timestamps[user_id]

    async def _is_throttled(self, user_id: str) -> bool:
        """
        检查指定用户的消息是否应该被限流

        Args:
            user_id: 用户ID

        Returns:
            是否应该限流
        """
        async with self._lock:
            # 检查全局限流
            global_count = len(self._global_timestamps)
            if global_count >= self._global_rate_limit:
                self.logger.warning(
                    f"全局消息限流触发: 当前速率 {global_count}/{self._window_size}秒 "
                    f"超过限制 {self._global_rate_limit}/{self._window_size}秒"
                )
                return True

            # 检查用户级别限流
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
        """
        记录消息的发送时间到对应队列

        Args:
            user_id: 用户ID
            current_time: 当前时间戳
        """
        async with self._lock:
            self._global_timestamps.append(current_time)
            self._user_timestamps[user_id].append(current_time)

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
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
        """重置所有状态（用于连接重建等场景）"""
        async with self._lock:
            self._global_timestamps.clear()
            self._user_timestamps.clear()
        self.reset_stats()
        self.logger.info("RateLimitTextPipeline 已重置状态")
