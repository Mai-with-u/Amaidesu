"""
Mock 输入 Provider（用于测试）
"""

from typing import Optional, Dict, Any
import asyncio
from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData


class MockInputProvider(InputProvider):
    """Mock 输入 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self._running = False
        self._test_data_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        """连接"""
        self._running = True

    async def disconnect(self):
        """断开连接"""
        self._running = False

    async def start(self):
        """启动"""
        await self.connect()

    async def stop(self):
        """停止"""
        await self.disconnect()

    def add_test_data(self, data: RawData):
        """添加测试数据"""
        self._test_data_queue.put_nowait(data)

    async def _read_data(self):
        """读取测试数据"""
        if self._test_data_queue.empty():
            await asyncio.sleep(0.1)
            return None
        return await self._test_data_queue.get()
