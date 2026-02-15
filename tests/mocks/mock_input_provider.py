"""
Mock 输入 Provider（用于测试）
"""

import asyncio
from typing import Any, AsyncIterator, Dict, Optional
from unittest.mock import MagicMock

from src.modules.di.context import ProviderContext
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage


def create_mock_context() -> ProviderContext:
    """创建 Mock ProviderContext 用于测试"""
    return ProviderContext(
        event_bus=MagicMock(),
        config_service=MagicMock(),
    )


class MockInputProvider(InputProvider):
    """Mock 输入 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, context: ProviderContext = None):
        if context is None:
            context = create_mock_context()
        super().__init__(config or {}, context=context)
        self._test_data_queue: asyncio.Queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        # 测试模式：如果为 True，provider 在无数据时自动退出
        self._auto_exit = config.get("auto_exit", False) if config else False

    def add_test_data(self, data: NormalizedMessage):
        """添加测试数据"""
        self._test_data_queue.put_nowait(data)

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        生成 NormalizedMessage 数据流
        """
        empty_count = 0
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

    async def cleanup(self) -> None:
        """清理资源"""
        # 设置停止事件以唤醒所有等待的操作
        self._stop_event.set()
        # 清空队列以避免阻塞
        while not self._test_data_queue.empty():
            try:
                self._test_data_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
