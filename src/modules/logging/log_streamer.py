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
        ws_handler: Optional["WebSocketHandler"] = None,
        min_level: str = "INFO",
        max_logs: int = 500,
    ):
        """
        Args:
            ws_handler: WebSocket 处理器实例（可选，可延迟设置）
            min_level: 最低日志级别
            max_logs: 最大缓存的日志条数
        """
        self.ws_handler = ws_handler
        self.min_level = min_level
        self.max_logs = max_logs
        self._handler_id: Optional[int] = None
        self._is_running = False
        self._log_buffer: list[dict[str, Any]] = []  # 历史日志缓冲区
        self._buffer_lock = asyncio.Lock()

    def set_ws_handler(self, ws_handler: "WebSocketHandler") -> None:
        """设置 WebSocket 处理器（用于延迟设置）"""
        self.ws_handler = ws_handler

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

    async def _add_to_buffer(self, log_entry: dict[str, Any]) -> None:
        """将日志添加到缓冲区"""
        async with self._buffer_lock:
            self._log_buffer.append(log_entry)
            if len(self._log_buffer) > self.max_logs:
                self._log_buffer.pop(0)

    async def get_recent_logs(self, count: int = 500) -> list[dict[str, Any]]:
        """获取最近的日志"""
        async with self._buffer_lock:
            return self._log_buffer[-count:].copy()

    async def broadcast_history(self, client_id: str) -> int:
        """向指定客户端推送历史日志"""
        import time

        history = await self.get_recent_logs()
        if not history:
            return 0

        # 导入在这里避免循环依赖
        from src.modules.dashboard.schemas.event import WebSocketMessage

        count = 0
        for log_entry in history:
            message = WebSocketMessage(
                type="log.entry",
                timestamp=time.time(),  # 使用当前时间作为消息发送时间
                data=log_entry,
            )
            try:
                await self.ws_handler._send_to_client(client_id, message)
                count += 1
            except Exception:
                pass
        return count

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
            # 添加到缓冲区
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._add_to_buffer(log_entry))
            except RuntimeError:
                # 没有运行中的事件循环，忽略
                pass
            # 广播到 WebSocket 客户端（仅当 ws_handler 已设置时）
            if self.ws_handler is not None:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.ws_handler.broadcast("log.entry", log_entry))
                except RuntimeError:
                    # 没有运行中的事件循环，忽略
                    pass
        except Exception:
            pass  # 避免日志系统崩溃
