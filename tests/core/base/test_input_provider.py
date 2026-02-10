"""
InputProvider 单元测试

测试 InputProvider 抽象基类的所有核心功能：
- 抽象方法验证（_collect_data）
- start() 返回 AsyncIterator
- stop() 和 cleanup() 调用
- setup() 空实现
- get_registration_info() NotImplementedError
- 生命周期管理

运行: uv run pytest tests/core/base/test_input_provider.py -v
"""

import asyncio
from typing import AsyncIterator

import pytest

from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.raw_data import RawData

# =============================================================================
# 测试用的 InputProvider 实现
# =============================================================================


class MockInputProvider(InputProvider):
    """模拟的 InputProvider 实现（用于测试）"""

    def __init__(self, config: dict, auto_stop: bool = False):
        """
        初始化 Mock InputProvider

        Args:
            config: Provider 配置
            auto_stop: 是否在生成一定数量后自动停止
        """
        super().__init__(config)
        self.auto_stop = auto_stop
        self.collected_count = 0
        self.cleanup_called = False

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """模拟数据采集"""
        max_items = 3 if self.auto_stop else 10

        for i in range(max_items):
            self.collected_count += 1
            yield RawData(
                content=f"测试消息 {i}",
                source="mock",
                data_type="text",
                metadata={"index": i},
            )
            await asyncio.sleep(0.01)  # 模拟异步操作

    async def _cleanup(self):
        """模拟清理资源"""
        self.cleanup_called = True
        self.is_running = False


class IncompleteInputProvider(InputProvider):
    """不完整的 InputProvider（未实现 _collect_data）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


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
    assert provider.is_running is False


@pytest.mark.asyncio
async def test_input_provider_default_config():
    """测试使用默认配置初始化"""
    provider = MockInputProvider({})

    assert provider.config == {}
    assert provider.is_running is False


# =============================================================================
# 抽象方法验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_abstract_method_not_implemented():
    """测试未实现抽象方法的子类无法实例化"""
    with pytest.raises(TypeError):
        # IncompleteInputProvider 未实现 _collect_data
        _ = IncompleteInputProvider({})


# =============================================================================
# start() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_start_returns_async_iterator(auto_stop_provider):
    """测试 start() 返回 AsyncIterator"""
    # start() 返回异步生成器，不需要 await
    data_stream = auto_stop_provider.start()

    # 验证返回的是异步迭代器
    assert hasattr(data_stream, "__aiter__")
    assert hasattr(data_stream, "__anext__")


@pytest.mark.asyncio
async def test_input_provider_start_yields_raw_data(auto_stop_provider):
    """测试 start() 生成 RawData"""
    count = 0
    async for raw_data in auto_stop_provider.start():
        assert isinstance(raw_data, RawData)
        assert raw_data.source == "mock"
        assert raw_data.data_type == "text"
        assert "测试消息" in raw_data.content
        count += 1

    # 验证生成的数据数量
    assert count == 3


@pytest.mark.asyncio
async def test_input_provider_start_sets_is_running():
    """测试 start() 设置 is_running 标志"""
    provider = MockInputProvider({}, auto_stop=True)

    assert provider.is_running is False

    # 启动并等待完成
    async for _ in provider.start():
        pass

    # 完成后 is_running 应该为 False（finally 块）
    assert provider.is_running is False


@pytest.mark.asyncio
async def test_input_provider_start_continuous():
    """测试 start() 持续生成数据（直到外部停止）"""
    provider = MockInputProvider({}, auto_stop=False)

    count = 0
    async for _raw_data in provider.start():
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
async def test_input_provider_stop_sets_is_running():
    """测试 stop() 设置 is_running 为 False"""
    provider = MockInputProvider({})

    # 先启动
    task = asyncio.create_task(provider._run_and_stop())
    await asyncio.sleep(0.05)  # 让启动完成

    assert provider.is_running is True

    # 停止
    await provider.stop()

    assert provider.is_running is False
    assert provider.cleanup_called is True

    # 取消任务
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_input_provider_stop_calls_cleanup():
    """测试 stop() 调用 _cleanup()"""
    provider = MockInputProvider({})

    await provider.stop()

    assert provider.cleanup_called is True
    assert provider.is_running is False


# =============================================================================
# cleanup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_cleanup_calls_internal_cleanup():
    """测试 cleanup() 调用内部的 _cleanup()"""
    provider = MockInputProvider({})

    await provider.cleanup()

    assert provider.cleanup_called is True
    assert provider.is_running is False


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
# setup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_setup_empty_implementation():
    """测试 setup() 是空实现（为了接口一致性）"""
    provider = MockInputProvider({})

    # setup() 不应该抛出异常
    await provider.setup(event_bus=None, dependencies=None)

    # setup() 不应该改变状态
    assert provider.is_running is False
    assert provider.config == {}


@pytest.mark.asyncio
async def test_input_provider_setup_with_parameters():
    """测试 setup() 接受参数但不使用"""
    provider = MockInputProvider({})

    mock_event_bus = object()
    mock_dependencies = {"dep1": "value1"}

    # 不应该抛出异常
    await provider.setup(event_bus=mock_event_bus, dependencies=mock_dependencies)


# =============================================================================
# get_registration_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_get_registration_info_not_implemented():
    """测试 get_registration_info() 默认抛出 NotImplementedError"""
    provider = MockInputProvider({})

    with pytest.raises(NotImplementedError) as exc_info:
        provider.get_registration_info()

    assert "必须实现 get_registration_info()" in str(exc_info.value)


@pytest.mark.asyncio
async def test_input_provider_get_registration_info_override():
    """测试子类可以重写 get_registration_info()"""

    class RegisteredProvider(MockInputProvider):
        """实现了注册方法的 Provider"""

        @classmethod
        def get_registration_info(cls):
            return {
                "layer": "input",
                "name": "mock_registered",
                "class": cls,
                "source": "test",
            }

    info = RegisteredProvider.get_registration_info()

    assert info["layer"] == "input"
    assert info["name"] == "mock_registered"
    assert info["class"] == RegisteredProvider
    assert info["source"] == "test"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_input_provider_full_lifecycle():
    """测试 InputProvider 完整生命周期"""
    provider = MockInputProvider({}, auto_stop=True)

    # 1. 初始化
    assert provider.is_running is False
    assert provider.cleanup_called is False

    # 2. 启动并收集数据
    collected_data = []
    async for raw_data in provider.start():
        collected_data.append(raw_data)

    # 3. 验证数据收集
    assert len(collected_data) == 3
    assert provider.is_running is False

    # 4. 清理
    await provider.cleanup()
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_input_provider_restart():
    """测试 InputProvider 可以重新启动"""
    provider = MockInputProvider({}, auto_stop=True)

    # 第一次运行
    count1 = 0
    async for _ in provider.start():
        count1 += 1
    assert count1 == 3

    # 清理
    await provider.cleanup()

    # 第二次运行（重置计数器）
    provider.collected_count = 0
    provider.auto_stop = True

    count2 = 0
    async for _ in provider.start():
        count2 += 1
    assert count2 == 3


# =============================================================================
# 辅助方法
# =============================================================================


# 添加一个辅助方法用于测试
async def _run_and_stop(self):
    """辅助方法：运行并自动停止"""
    async for _ in self.start():
        await asyncio.sleep(0.01)


# 动态添加辅助方法到 MockInputProvider
MockInputProvider._run_and_stop = _run_and_stop


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
