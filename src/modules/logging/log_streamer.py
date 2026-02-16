"""
日志流广播器 - 将日志记录广播到 WebSocket
"""

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger as loguru_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler


class LogStreamer:
    """日志流广播器 - 捕获日志并广播到 WebSocket"""

    def __init__(
        self,
        ws_handler: "WebSocketHandler",
        min_level: str = "INFO",
        max_logs: int = 500,
    ):
        """
        Args:
            ws_handler: WebSocket 处理器实例
            min_level: 最低日志级别
            max_logs: 最大缓存的日志条数（预留，暂未使用）
        """
        self.ws_handler = ws_handler
        self.min_level = min_level
        self.max_logs = max_logs
        self._handler_id: Optional[int] = None
        self._is_running = False

    async def start(self) -> None:
        """启动日志流"""
        if self._is_running:
            return
        self._is_running = True
        # 添加 loguru handler
        self._handler_id = loguru_logger.add(
            self._sink,
            level=self.min_level,
            format="{message}",  # 我们自己处理格式
            filter=self._filter,
        )

    async def stop(self) -> None:
        """停止日志流"""
        if not self._is_running:
            return
        self._is_running = False
        if self._handler_id is not None:
            loguru_logger.remove(self._handler_id)
            self._handler_id = None

    def _filter(self, record: dict) -> bool:
        """过滤日志记录"""
        # 过滤掉自己的日志，避免递归
        module = record["extra"].get("module", "")
        if "LogStreamer" in module or "websocket" in module.lower():
            return False
        return True

    def _sink(self, message: Any) -> None:
        """loguru sink - 处理日志记录"""
        try:
            record = message.record
            log_entry = {
                "timestamp": record["time"].isoformat(),
                "level": record["level"].name,
                "module": record["extra"].get("module", "unknown"),
                "message": record["message"],
            }
            # 使用 asyncio 安排广播
            # 注意：这里需要检查是否有运行中的事件循环
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.ws_handler.broadcast("log.entry", log_entry))
            except RuntimeError:
                # 没有运行中的事件循环，忽略
                pass
        except Exception:
            pass  # 避免日志系统崩溃
