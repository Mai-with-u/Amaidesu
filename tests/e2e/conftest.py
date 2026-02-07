"""
Pytest configuration and fixtures for E2E tests
"""
import asyncio
import pytest
from typing import AsyncGenerator

from src.core.event_bus import EventBus
from src.domains.input.input_provider_manager import InputProviderManager
from src.domains.input.input_layer import InputLayer
from src.domains.decision.decision_manager import DecisionManager
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage


@pytest.fixture
def event_bus() -> EventBus:
    """创建 EventBus 实例"""
    return EventBus()


@pytest.fixture
async def input_layer(event_bus: EventBus) -> AsyncGenerator[InputLayer, None]:
    """创建 InputLayer 实例"""
    from src.domains.input.input_layer import InputLayer

    layer = InputLayer(event_bus)
    await layer.setup()
    yield layer
    await layer.cleanup()


@pytest.fixture
async def decision_manager(event_bus: EventBus) -> AsyncGenerator[DecisionManager, None]:
    """创建 DecisionManager 实例"""
    manager = DecisionManager(event_bus, llm_service=None)
    yield manager
    # 清理
    if manager.get_current_provider():
        await manager.cleanup()


@pytest.fixture
def sample_raw_data() -> RawData:
    """创建示例 RawData"""
    return RawData(
        content="你好，VTuber",
        data_type="text",
        source="test_user",
        metadata={"user_id": "test_user", "user_nickname": "测试用户"}
    )


@pytest.fixture
def sample_normalized_message() -> NormalizedMessage:
    """创建示例 NormalizedMessage"""
    from tests.e2e.test_helpers import create_normalized_message
    return create_normalized_message("你好，VTuber", "test_user", 0.8)


@pytest.fixture
async def mock_input_provider_manager(event_bus: EventBus) -> InputProviderManager:
    """创建使用 Mock Provider 的 InputProviderManager"""
    manager = InputProviderManager(event_bus)

    # 加载配置（使用 mock_danmaku）
    config = {
        'enabled': True,
        'inputs': ['mock_danmaku'],
        'inputs_config': {
            'mock_danmaku': {
                'type': 'mock_danmaku',
                'enabled': True,
                'interval': 5.0
            }
        }
    }

    await manager.load_from_config(config)
    # 注意：不启动 Provider，只返回已创建的实例
    return manager


@pytest.fixture
async def mock_output_provider():
    """创建 MockOutputProvider 实例"""
    from src.domains.output.providers.mock import MockOutputProvider

    provider = MockOutputProvider({})
    await provider.setup()
    yield provider
    await provider.cleanup()


@pytest.fixture
def wait_for_event():
    """辅助函数：等待特定事件"""

    async def _waiter(event_bus: EventBus, event_name: str, timeout: float = 2.0):
        """等待事件触发"""
        future = asyncio.Future()

        async def callback(event_name, data, source):
            if not future.done():
                future.set_result((event_name, data, source))

        event_bus.on(event_name, callback)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            event_bus.off(event_name, callback)

    return _waiter
