"""
日志流广播器 - 将日志记录广播到 WebSocket
"""

import asyncio
import json
import sys
import time as time_mod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger as loguru_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler


class LogStreamer:
    """日志流广播器 - 捕获日志并广播到 WebSocket"""

    # 日志持久化默认参数
    DEFAULT_PERSIST_DIR = "data/logs"

    def __init__(
        self,
        ws_handler: Optional["WebSocketHandler"] = None,
        min_level: str = "INFO",
        max_logs: int = 500,
        persist: bool = False,
        persist_dir: str = DEFAULT_PERSIST_DIR,
    ):
        """
        Args:
            ws_handler: WebSocket 处理器实例（可选，可延迟设置）
            min_level: 最低日志级别
            max_logs: 最大缓存的日志条数
            persist: 是否启用 JSONL 文件持久化
            persist_dir: 持久化目录（相对项目根）
        """
        self.ws_handler = ws_handler
        self.min_level = min_level
        self.max_logs = max_logs
        self.persist = persist
        self._handler_id: Optional[int] = None
        self._is_running = False
        self._log_buffer: list[dict[str, Any]] = []  # 历史日志缓冲区
        self._buffer_lock = asyncio.Lock()

        # 持久化路径
        self._persist_dir: Optional[Path] = None
        self._current_date: Optional[str] = None
        self._current_file_path: Optional[Path] = None
        if self.persist:
            project_root = Path(__file__).resolve().parents[3]
            self._persist_dir = (project_root / persist_dir).resolve()
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # ------------------------------------------------------------------ #
    # 内部:磁盘恢复与持久化                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _date_string(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")

    def _load_from_disk(self) -> None:
        """启动时从当日 JSONL 文件恢复日志到内存缓冲。"""
        if self._persist_dir is None:
            return
        today = self._date_string(time_mod.time())
        file_path = self._persist_dir / f"{today}.jsonl"
        if not file_path.exists():
            return
        try:
            count = 0
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self._log_buffer.append(entry)
                        count += 1
                    except json.JSONDecodeError:
                        continue
            # 限制缓冲不超过 max_logs
            if len(self._log_buffer) > self.max_logs:
                self._log_buffer = self._log_buffer[-self.max_logs :]
            loguru_logger.info(f"从磁盘恢复 {count} 条历史日志 ({file_path})")
        except Exception as exc:
            loguru_logger.warning(f"从磁盘恢复日志失败 ({file_path}): {exc!r}")

    def _append_to_file(self, entry: dict[str, Any]) -> None:
        """同步将单条日志追加到当日 JSONL 文件。"""
        if not self.persist or self._persist_dir is None:
            return
        try:
            now = time_mod.time()
            date_str = self._date_string(now)
            if date_str != self._current_date or self._current_file_path is None:
                self._current_date = date_str
                self._current_file_path = self._persist_dir / f"{date_str}.jsonl"
            with open(self._current_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            loguru_logger.warning(f"写入日志持久化文件失败: {exc!r}")

    # ------------------------------------------------------------------ #
    # 公开 API                                                            #
    # ------------------------------------------------------------------ #

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
        """将日志添加到缓冲区并持久化到磁盘。"""
        if self.persist:
            self._append_to_file(log_entry)
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
            except Exception as e:
                loguru_logger.debug(f"WS broadcast to client {client_id} failed: {e!r}")
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
        except Exception as e:
            sys.stderr.write(f"[log_streamer] sink error: {e!r}\n")
