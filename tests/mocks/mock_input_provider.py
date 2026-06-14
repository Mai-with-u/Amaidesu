"""
Mock 输入 Collector（用于测试）
"""

import asyncio
from typing import Any, AsyncIterator, Dict

from src.modules.events.event_bus import EventBus
from src.modules.types.base.normalized_message import NormalizedMessage


class MockInputCollector:
    """Mock 输入 Collector（用于测试）"""

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._test_data_queue: asyncio.Queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self.is_started = False
        # 测试模式：如果为 True，collector 在无数据时自动退出
        self._auto_exit = config.get("auto_exit", False) if config else False
        # 用于 get_collector_by_source 匹配
        self._source = config.get("source", "mock_source")

    @property
    def source(self) -> str:
        """返回 Collector 的数据源标识"""
        return self._source

    def add_test_data(self, data: NormalizedMessage):
        """添加测试数据"""
        self._test_data_queue.put_nowait(data)

    async def start(self) -> None:
        """启动 Collector"""
        self.is_started = True

    async def stop(self) -> None:
        """停止 Collector"""
        self.is_started = False
        self._stop_event.set()

    async def cleanup(self) -> None:
        """清理资源"""
        self.is_started = False
        # 设置停止事件以唤醒所有等待的操作
        self._stop_event.set()
        # 清空队列以避免阻塞
        while not self._test_data_queue.empty():
            try:
                self._test_data_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """
        收集 NormalizedMessage 数据流
        """
        self.is_started = True
        empty_count = 0
        try:
            while self.is_started:
                try:
                    # 使用短超时定期检查 is_started
                    data = await asyncio.wait_for(self._test_data_queue.get(), timeout=0.05)
                    empty_count = 0  # 重置计数
                    yield data
                except asyncio.TimeoutError:
                    # 超时正常，继续循环检查 is_started
                    # 如果启用 auto_exit 且连续多次超时，则退出
                    if self._auto_exit:
                        empty_count += 1
                        if empty_count >= 3:  # 连续3次超时（150ms）
                            break
        finally:
            self.is_started = False
