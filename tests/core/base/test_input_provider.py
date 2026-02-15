"""
InputProvider 单元测试

测试 InputProvider 抽象基类的所有核心功能：
- 抽象方法验证（generate）
- start() 启动、stream() 返回 AsyncIterator
- stop() 和 cleanup() 调用
- 生命周期管理
- context 必填验证

运行: uv run pytest tests/core/base/test_input_provider.py -v
"""

import asyncio
from typing import AsyncIterator
from unittest.mock import MagicMock

import pytest

from src.modules.di.context import ProviderContext
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage

# =============================================================================
# 测试用的 InputProvider 实现
# =============================================================================


def create_mock_context() -> ProviderContext:
    """创建 Mock ProviderContext 用于测试"""
    return ProviderContext(
        event_bus=MagicMock(),
        config_service=MagicMock(),
    )


class MockInputProvider(InputProvider):
    """模拟的 InputProvider 实现（用于测试）"""

    def __init__(self, config: dict = None, auto_stop: bool = False, context: ProviderContext = None):
        """
        初始化 Mock InputProvider

        Args:
            config: Provider 配置
            auto_stop: 是否在生成一定数量后自动停止
            context: 依赖上下文（可选，默认使用 mock）
        """
        if context is None:
            context = create_mock_context()
        super().__init__(config or {}, context=context)
        self.auto_stop = auto_stop
        self.collected_count = 0
        self.cleanup_called = False

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        生成 NormalizedMessage 数据流
        """
        max_items = 3 if self.auto_stop else 10

        for i in range(max_items):
            self.collected_count += 1
            yield NormalizedMessage(
                text=f"测试消息 {i}",
                source="mock",
                data_type="text",
                importance=0.5,
                raw={"user": "MockUser", "user_id": "mock_id"},
            )
            await asyncio.sleep(0.01)  # 模拟异步操作

    async def cleanup(self) -> None:
        """清理资源"""
        self.cleanup_called = True


class IncompleteInputProvider(InputProvider):
    """不完整的 InputProvider（未实现 generate）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def mock_context():
    """创建 Mock ProviderContext"""
    return create_mock_context()


@pytest.fixture
def mock_provider():
    """创建标准的 MockInputProvider 实例"""
    return MockInputProvider(config={"test": "config"})


@pytest.fixture
def auto_stop_provider():
    """创建自动停止的 MockInputProvider"""
    return MockInputProvider(config={"test": "config"}, auto_stop=True)


# =============================================================================
# 实例化和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_initialization():
    """测试 InputProvider 初始化"""
    config = {"source": "test", "interval": 1.0}
    provider = MockInputProvider(config)

    assert provider.config == config
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_input_provider_default_config():
    """测试使用默认配置初始化"""
    provider = MockInputProvider({})

    assert provider.config == {}
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_input_provider_requires_context():
    """测试 InputProvider 必须接收 context 参数"""
    with pytest.raises(ValueError, match="InputProvider 必须接收 context 参数"):
        # 直接调用基类，不传 context
        InputProvider.__init__(MockInputProvider.__new__(MockInputProvider), {})


# =============================================================================
# 抽象方法验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_abstract_method_not_implemented():
    """测试未实现抽象方法的子类无法实例化"""
    with pytest.raises(TypeError):
        # IncompleteInputProvider 未实现 generate()
        _ = IncompleteInputProvider({})


# =============================================================================
# start() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_start_returns_none(auto_stop_provider):
    """测试 start() 返回 None"""
    # start() 返回 None，需要 await
    result = await auto_stop_provider.start()
    assert result is None
    assert auto_stop_provider.is_started is True


@pytest.mark.asyncio
async def test_input_provider_stream_raises_before_start():
    """测试 stream() 在 start() 之前调用抛出异常"""
    provider = MockInputProvider({}, auto_stop=True)

    # 未启动时调用 stream() 应该抛出异常
    with pytest.raises(RuntimeError, match="Provider 未启动"):
        async for _ in provider.stream():
            pass


@pytest.mark.asyncio
async def test_input_provider_stream_returns_async_iterator(auto_stop_provider):
    """测试 stream() 返回 AsyncIterator"""
    # 先启动
    await auto_stop_provider.start()

    # stream() 返回异步迭代器
    data_stream = auto_stop_provider.stream()

    # 验证返回的是异步迭代器
    assert hasattr(data_stream, "__aiter__")
    assert hasattr(data_stream, "__anext__")


@pytest.mark.asyncio
async def test_input_provider_stream_yields_normalized_message(auto_stop_provider):
    """测试 stream() 生成 NormalizedMessage"""
    # 先启动
    await auto_stop_provider.start()

    count = 0
    async for message in auto_stop_provider.stream():
        assert isinstance(message, NormalizedMessage)
        assert message.source == "mock"
        assert message.data_type == "text"
        assert "测试消息" in message.text
        count += 1

    # 验证生成的数据数量
    assert count == 3


@pytest.mark.asyncio
async def test_input_provider_start_sets_is_started():
    """测试 start() 设置 is_started 标志"""
    provider = MockInputProvider({}, auto_stop=True)

    assert provider.is_started is False

    # 启动 Provider
    await provider.start()

    # 启动后 is_started 应该为 True
    assert provider.is_started is True

    # 等待数据流完成
    async for _ in provider.stream():
        pass

    # 完成后 is_started 应该为 False（finally 块）
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_input_provider_stream_continuous():
    """测试 stream() 持续生成数据（直到外部停止）"""
    provider = MockInputProvider({}, auto_stop=False)

    # 先启动
    await provider.start()

    count = 0
    async for _raw_data in provider.stream():
        count += 1
        if count >= 5:  # 手动停止
            await provider.stop()
            break

    # 验证生成了指定数量的数据
    assert count == 5


# =============================================================================
# stop() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_stop_sets_is_started():
    """测试 stop() 设置 is_started 为 False"""
    provider = MockInputProvider({})

    # 先启动
    await provider.start()

    assert provider.is_started is True

    # 停止
    await provider.stop()

    assert provider.is_started is False
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_input_provider_stop_calls_cleanup():
    """测试 stop() 调用 cleanup()"""
    provider = MockInputProvider({})

    await provider.stop()

    assert provider.cleanup_called is True
    assert provider.is_started is False


# =============================================================================
# cleanup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_cleanup_calls_internal_cleanup():
    """测试 cleanup() 调用内部的清理逻辑"""
    provider = MockInputProvider({})

    await provider.cleanup()

    assert provider.cleanup_called is True
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_input_provider_cleanup_idempotent():
    """测试 cleanup() 可以多次调用（幂等）"""
    provider = MockInputProvider({})

    await provider.cleanup()
    await provider.cleanup()
    await provider.cleanup()

    # 不应该抛出异常
    assert provider.cleanup_called is True


# =============================================================================
# get_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_get_info():
    """测试 get_info() 返回正确的 Provider 信息"""
    provider = MockInputProvider({"test": "value"})

    info = provider.get_info()

    assert info["name"] == "MockInputProvider"
    assert info["is_started"] is False
    assert info["type"] == "input_provider"


@pytest.mark.asyncio
async def test_input_provider_get_info_after_start():
    """测试启动后 get_info() 返回正确状态"""
    provider = MockInputProvider({})
    await provider.start()

    info = provider.get_info()

    assert info["is_started"] is True


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_full_lifecycle():
    """测试 InputProvider 完整生命周期"""
    provider = MockInputProvider({}, auto_stop=True)

    # 1. 初始化
    assert provider.is_started is False
    assert provider.cleanup_called is False

    # 2. 启动并收集数据
    await provider.start()
    collected_data = []
    async for raw_data in provider.stream():
        collected_data.append(raw_data)

    # 3. 验证数据收集
    assert len(collected_data) == 3
    assert provider.is_started is False

    # 4. 清理
    await provider.cleanup()
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_input_provider_restart():
    """测试 InputProvider 可以重新启动"""
    provider = MockInputProvider({}, auto_stop=True)

    # 第一次运行
    await provider.start()
    count1 = 0
    async for _ in provider.stream():
        count1 += 1
    assert count1 == 3

    # 清理
    await provider.cleanup()

    # 第二次运行（重置计数器）
    provider.collected_count = 0
    provider.auto_stop = True

    # 重新启动
    await provider.start()
    count2 = 0
    async for _ in provider.stream():
        count2 += 1
    assert count2 == 3


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
