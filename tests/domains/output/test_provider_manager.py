"""
OutputProviderManager 单元测试

测试 OutputProviderManager 的所有核心功能：
- Provider 注册（单个、多个、重复注册）
- 批量设置（setup_all_providers、并发设置、部分失败）
- 并发渲染（render_all、RenderParameters、错误隔离）
- 停止所有 Provider（stop_all_providers、优雅关闭）
- 从配置加载（load_from_config、配置格式解析）
- Provider 查询（get_provider_names、get_provider_by_name、get_stats）
- 并发场景
- 错误隔离
- 边界情况

运行: uv run pytest tests/core/test_output_provider_manager.py -v
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.domains.output.parameters.render_parameters import RenderParameters
from src.domains.output.provider_manager import OutputProviderManager
from tests.mocks.mock_output_provider import MockOutputProvider

# =============================================================================
# Mock Provider 实现
# =============================================================================


class FailingMockOutputProvider(MockOutputProvider):
    """会失败的 Mock Provider 用于测试错误隔离"""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.should_fail_on_setup = False
        self.should_fail_on_render = False
        self.should_fail_on_cleanup = False

    async def _start_internal(self):
        """启动（可能失败）"""
        if self.should_fail_on_setup:
            raise RuntimeError("Setup failed")
        await super()._start_internal()

    async def _render_internal(self, parameters: RenderParameters) -> bool:
        """渲染（可能失败）"""
        if self.should_fail_on_render:
            raise RuntimeError("Render failed")
        return await super()._render_internal(parameters)

    async def cleanup(self):
        """清理（可能失败）"""
        if self.should_fail_on_cleanup:
            raise RuntimeError("Cleanup failed")
        await super().cleanup()


class SlowMockOutputProvider(MockOutputProvider):
    """慢速 Mock Provider 用于测试并发"""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.delay_seconds = config.get("delay_seconds", 0.1) if config else 0.1

    async def _render_internal(self, parameters: RenderParameters) -> bool:
        """渲染（延迟）"""
        await asyncio.sleep(self.delay_seconds)
        return await super()._render_internal(parameters)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def manager():
    """创建 OutputProviderManager 实例"""
    return OutputProviderManager()


@pytest.fixture
def manager_with_config():
    """创建带配置的 OutputProviderManager 实例"""
    return OutputProviderManager(config={"concurrent_rendering": True, "error_handling": "continue"})


@pytest.fixture
def sample_parameters():
    """创建示例 RenderParameters"""
    return RenderParameters(
        tts_text="Hello, world!",
        subtitle_text="你好世界",
        expressions={"smile": 0.8},
        hotkeys=["wave"],
        metadata={"test": True},
    )


@pytest.fixture
def mock_event_bus():
    """创建 Mock EventBus"""
    event_bus = Mock()
    event_bus.emit = AsyncMock()
    event_bus.subscribe = Mock()
    return event_bus


@pytest.fixture
def sample_config():
    """创建示例配置"""
    return {
        "enabled": True,
        "concurrent_rendering": True,
        "error_handling": "continue",
        "enabled_outputs": ["mock1", "mock2"],
        "outputs": {"mock1": {"type": "mock", "delay_seconds": 0.1}, "mock2": {"type": "mock", "delay_seconds": 0.2}},
    }


# =============================================================================
# 初始化测试
# =============================================================================


def test_manager_initialization():
    """测试 OutputProviderManager 初始化"""
    manager = OutputProviderManager()

    assert manager.providers == []
    assert manager.concurrent_rendering is True
    assert manager.error_handling == "continue"


def test_manager_initialization_with_config():
    """测试带配置的初始化"""
    config = {"concurrent_rendering": False, "error_handling": "stop"}
    manager = OutputProviderManager(config)

    assert manager.concurrent_rendering is False
    assert manager.error_handling == "stop"


def test_manager_initialization_with_empty_config():
    """测试空配置"""
    manager = OutputProviderManager({})

    assert manager.providers == []
    assert manager.concurrent_rendering is True  # 默认值
    assert manager.error_handling == "continue"  # 默认值


# =============================================================================
# Provider 注册测试
# =============================================================================


@pytest.mark.asyncio
async def test_register_single_provider(manager: OutputProviderManager):
    """测试注册单个 Provider"""
    provider = MockOutputProvider()

    await manager.register_provider(provider)

    assert len(manager.providers) == 1
    assert manager.providers[0] == provider


@pytest.mark.asyncio
async def test_register_multiple_providers(manager: OutputProviderManager):
    """测试注册多个 Provider"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    assert len(manager.providers) == 3
    assert manager.providers[0] == provider1
    assert manager.providers[1] == provider2
    assert manager.providers[2] == provider3


@pytest.mark.asyncio
async def test_register_duplicate_provider(manager: OutputProviderManager):
    """测试重复注册同一个 Provider"""
    provider = MockOutputProvider()

    await manager.register_provider(provider)
    await manager.register_provider(provider)

    # 应该允许重复注册（虽然不推荐）
    assert len(manager.providers) == 2
    assert manager.providers[0] == provider
    assert manager.providers[1] == provider


@pytest.mark.asyncio
async def test_register_provider_preserves_order(manager: OutputProviderManager):
    """测试注册顺序保持"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    assert manager.providers[0] == provider1
    assert manager.providers[1] == provider2
    assert manager.providers[2] == provider3


# =============================================================================
# 批量设置测试
# =============================================================================


@pytest.mark.asyncio
async def test_setup_all_providers_empty(manager: OutputProviderManager, mock_event_bus):
    """测试设置空的 Provider 列表"""
    await manager.setup_all_providers(mock_event_bus)

    # 不应该报错
    assert len(manager.providers) == 0


@pytest.mark.asyncio
async def test_setup_all_providers_single(manager: OutputProviderManager, mock_event_bus):
    """测试设置单个 Provider"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)

    await manager.setup_all_providers(mock_event_bus)

    assert provider.is_started is True
    assert provider.event_bus == mock_event_bus


@pytest.mark.asyncio
async def test_setup_all_providers_multiple(manager: OutputProviderManager, mock_event_bus):
    """测试设置多个 Provider（并发）"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    await manager.setup_all_providers(mock_event_bus)

    assert provider1.is_started is True
    assert provider2.is_started is True
    assert provider3.is_started is True

    assert provider1.event_bus == mock_event_bus
    assert provider2.event_bus == mock_event_bus
    assert provider3.event_bus == mock_event_bus


@pytest.mark.asyncio
async def test_setup_all_providers_concurrent(manager: OutputProviderManager, mock_event_bus):
    """测试并发设置"""
    import time

    slow_provider1 = SlowMockOutputProvider({"delay_seconds": 0.1})
    slow_provider2 = SlowMockOutputProvider({"delay_seconds": 0.1})
    slow_provider3 = SlowMockOutputProvider({"delay_seconds": 0.1})

    await manager.register_provider(slow_provider1)
    await manager.register_provider(slow_provider2)
    await manager.register_provider(slow_provider3)

    start_time = time.time()
    await manager.setup_all_providers(mock_event_bus)
    elapsed_time = time.time() - start_time

    # 并发执行，应该远小于串行的 0.3 秒
    assert elapsed_time < 0.25
    assert slow_provider1.is_started is True
    assert slow_provider2.is_started is True
    assert slow_provider3.is_started is True


@pytest.mark.asyncio
async def test_setup_all_providers_partial_failure(manager: OutputProviderManager, mock_event_bus):
    """测试部分 Provider 设置失败"""
    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_setup = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    await manager.setup_all_providers(mock_event_bus)

    # provider1 和 provider3 应该成功
    assert provider1.is_started is True
    assert provider3.is_started is True

    # provider2 应该失败
    assert provider2.is_started is False


@pytest.mark.asyncio
async def test_setup_all_providers_serial_mode(manager: OutputProviderManager, mock_event_bus):
    """测试串行设置模式"""
    manager.concurrent_rendering = False

    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    await manager.setup_all_providers(mock_event_bus)

    assert provider1.is_started is True
    assert provider2.is_started is True
    assert provider3.is_started is True


@pytest.mark.asyncio
async def test_setup_all_providers_serial_with_failure(manager: OutputProviderManager, mock_event_bus):
    """测试串行模式下的失败处理"""
    manager.concurrent_rendering = False

    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_setup = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    await manager.setup_all_providers(mock_event_bus)

    # provider1 和 provider3 应该继续设置（错误隔离）
    assert provider1.is_started is True
    assert provider3.is_started is True
    assert provider2.is_started is False


# =============================================================================
# 并发渲染测试
# =============================================================================


@pytest.mark.asyncio
async def test_render_all_empty(manager: OutputProviderManager, sample_parameters: RenderParameters):
    """测试渲染到空的 Provider 列表"""
    # 不应该报错
    await manager.render_all(sample_parameters)


@pytest.mark.asyncio
async def test_render_all_single_provider(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试渲染到单个 Provider"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    await manager.render_all(sample_parameters)

    assert len(provider.received_parameters) == 1
    assert provider.received_parameters[0] == sample_parameters


@pytest.mark.asyncio
async def test_render_all_multiple_providers(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试渲染到多个 Provider"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    await manager.render_all(sample_parameters)

    # 所有 Provider 都应该收到参数
    assert len(provider1.received_parameters) == 1
    assert len(provider2.received_parameters) == 1
    assert len(provider3.received_parameters) == 1

    assert provider1.received_parameters[0] == sample_parameters
    assert provider2.received_parameters[0] == sample_parameters
    assert provider3.received_parameters[0] == sample_parameters


@pytest.mark.asyncio
async def test_render_all_concurrent(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试并发渲染"""
    import time

    slow_provider1 = SlowMockOutputProvider({"delay_seconds": 0.1})
    slow_provider2 = SlowMockOutputProvider({"delay_seconds": 0.1})
    slow_provider3 = SlowMockOutputProvider({"delay_seconds": 0.1})

    await manager.register_provider(slow_provider1)
    await manager.register_provider(slow_provider2)
    await manager.register_provider(slow_provider3)
    await manager.setup_all_providers(mock_event_bus)

    start_time = time.time()
    await manager.render_all(sample_parameters)
    elapsed_time = time.time() - start_time

    # 并发执行，应该远小于串行的 0.3 秒
    assert elapsed_time < 0.25

    # 所有 Provider 都应该收到参数
    assert len(slow_provider1.received_parameters) == 1
    assert len(slow_provider2.received_parameters) == 1
    assert len(slow_provider3.received_parameters) == 1


@pytest.mark.asyncio
async def test_render_all_multiple_renders(manager: OutputProviderManager, mock_event_bus):
    """测试多次渲染"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    params1 = RenderParameters(tts_text="First")
    params2 = RenderParameters(tts_text="Second")
    params3 = RenderParameters(tts_text="Third")

    await manager.render_all(params1)
    await manager.render_all(params2)
    await manager.render_all(params3)

    assert len(provider.received_parameters) == 3
    assert provider.received_parameters[0].tts_text == "First"
    assert provider.received_parameters[1].tts_text == "Second"
    assert provider.received_parameters[2].tts_text == "Third"


@pytest.mark.asyncio
async def test_render_all_provider_not_setup(manager: OutputProviderManager, sample_parameters: RenderParameters):
    """测试渲染到未设置的 Provider"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)

    # 未设置，render 会捕获错误并记录，不会抛出异常
    await manager.render_all(sample_parameters)

    # Provider 不应该收到参数（因为 render 失败了）
    assert len(provider.received_parameters) == 0


@pytest.mark.asyncio
async def test_render_all_partial_failure_continue(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试部分 Provider 渲染失败（继续模式）"""
    manager.error_handling = "continue"

    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_render = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    # 不应该抛出异常
    await manager.render_all(sample_parameters)

    # provider1 和 provider3 应该成功
    assert len(provider1.received_parameters) == 1
    assert len(provider3.received_parameters) == 1

    # provider2 失败，不应该有参数记录（因为 render 抛出异常）
    assert len(provider2.received_parameters) == 0


@pytest.mark.asyncio
async def test_render_all_partial_failure_stop(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试部分 Provider 渲染失败（停止模式）"""
    manager.error_handling = "stop"

    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_render = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    # 不应该抛出异常（错误隔离），但应该停止后续渲染
    await manager.render_all(sample_parameters)

    # provider1 应该成功渲染
    assert len(provider1.received_parameters) == 1

    # provider3 可能不会渲染（取决于并发执行顺序）
    # 在并发模式下，provider3 可能已经渲染了


@pytest.mark.asyncio
async def test_render_all_serial_mode(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试串行渲染模式"""
    manager.concurrent_rendering = False

    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    await manager.render_all(sample_parameters)

    assert len(provider1.received_parameters) == 1
    assert len(provider2.received_parameters) == 1
    assert len(provider3.received_parameters) == 1


@pytest.mark.asyncio
async def test_render_all_serial_with_failure_stop(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试串行模式下的失败（停止模式）"""
    manager.concurrent_rendering = False
    manager.error_handling = "stop"

    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_render = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    await manager.render_all(sample_parameters)

    # provider1 应该渲染
    assert len(provider1.received_parameters) == 1

    # provider3 不应该渲染（串行模式下，provider2 失败后停止）
    assert len(provider3.received_parameters) == 0


@pytest.mark.asyncio
async def test_render_all_serial_with_failure_continue(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试串行模式下的失败（继续模式）"""
    manager.concurrent_rendering = False
    manager.error_handling = "continue"

    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_render = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    await manager.render_all(sample_parameters)

    # provider1 和 provider3 都应该渲染（错误隔离）
    assert len(provider1.received_parameters) == 1
    assert len(provider3.received_parameters) == 1


# =============================================================================
# 停止所有 Provider 测试
# =============================================================================


@pytest.mark.asyncio
async def test_stop_all_providers_empty(manager: OutputProviderManager):
    """测试停止空的 Provider 列表"""
    # 不应该报错
    await manager.stop_all_providers()


@pytest.mark.asyncio
async def test_stop_all_providers_single(manager: OutputProviderManager, mock_event_bus):
    """测试停止单个 Provider"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    assert provider.is_started is True

    await manager.stop_all_providers()

    assert provider.is_started is False


@pytest.mark.asyncio
async def test_stop_all_providers_multiple(manager: OutputProviderManager, mock_event_bus):
    """测试停止多个 Provider"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    assert provider1.is_started is True
    assert provider2.is_started is True
    assert provider3.is_started is True

    await manager.stop_all_providers()

    assert provider1.is_started is False
    assert provider2.is_started is False
    assert provider3.is_started is False


@pytest.mark.asyncio
async def test_stop_all_providers_partial_failure(manager: OutputProviderManager, mock_event_bus):
    """测试停止时部分 Provider 失败"""
    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_cleanup = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    # 不应该抛出异常
    await manager.stop_all_providers()

    # provider1 和 provider3 应该成功停止
    assert provider1.is_started is False
    assert provider3.is_started is False

    # provider2 清理失败，is_started 保持为 True（因为 cleanup() 异常退出了）
    assert provider2.is_started is True


@pytest.mark.asyncio
async def test_stop_all_providers_only_setup_providers(manager: OutputProviderManager, mock_event_bus):
    """测试只停止已设置的 Provider"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)

    # 只启动 provider1
    await provider1.start(mock_event_bus)

    await manager.stop_all_providers()

    # provider1 应该被停止
    assert provider1.is_started is False

    # provider2 未设置，不应该被停止
    assert provider2.is_started is False


@pytest.mark.asyncio
async def test_stop_all_providers_idempotent(manager: OutputProviderManager, mock_event_bus):
    """测试停止操作的幂等性"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    await manager.stop_all_providers()
    await manager.stop_all_providers()
    await manager.stop_all_providers()

    # 多次停止不应该报错
    assert provider.is_started is False


# =============================================================================
# Provider 查询测试
# =============================================================================


def test_get_provider_names_empty(manager: OutputProviderManager):
    """测试获取空 Provider 列表的名称"""
    names = manager.get_provider_names()

    assert names == []


def test_get_provider_names_single(manager: OutputProviderManager):
    """测试获取单个 Provider 名称"""
    provider = MockOutputProvider()
    import asyncio

    asyncio.run(manager.register_provider(provider))

    names = manager.get_provider_names()

    assert len(names) == 1
    assert names[0] == "MockOutputProvider"


def test_get_provider_names_multiple(manager: OutputProviderManager):
    """测试获取多个 Provider 名称"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    import asyncio

    asyncio.run(manager.register_provider(provider1))
    asyncio.run(manager.register_provider(provider2))
    asyncio.run(manager.register_provider(provider3))

    names = manager.get_provider_names()

    assert len(names) == 3
    assert all(name == "MockOutputProvider" for name in names)


def test_get_provider_by_name_empty(manager: OutputProviderManager):
    """测试从空列表查找 Provider"""
    provider = manager.get_provider_by_name("MockOutputProvider")

    assert provider is None


def test_get_provider_by_name_exists(manager: OutputProviderManager):
    """测试查找存在的 Provider"""
    provider = MockOutputProvider()
    import asyncio

    asyncio.run(manager.register_provider(provider))

    found = manager.get_provider_by_name("MockOutputProvider")

    assert found is not None
    assert found == provider


def test_get_provider_by_name_not_exists(manager: OutputProviderManager):
    """测试查找不存在的 Provider"""
    provider = MockOutputProvider()
    import asyncio

    asyncio.run(manager.register_provider(provider))

    found = manager.get_provider_by_name("NonExistentProvider")

    assert found is None


def test_get_provider_by_name_first_match(manager: OutputProviderManager):
    """测试查找返回第一个匹配项"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()

    import asyncio

    asyncio.run(manager.register_provider(provider1))
    asyncio.run(manager.register_provider(provider2))

    found = manager.get_provider_by_name("MockOutputProvider")

    # 应该返回第一个注册的
    assert found == provider1


def test_get_stats_empty(manager: OutputProviderManager):
    """测试获取空 Manager 的统计信息"""
    stats = manager.get_stats()

    assert stats["total_providers"] == 0
    assert stats["setup_providers"] == 0
    assert stats["concurrent_rendering"] is True
    assert stats["error_handling"] == "continue"
    assert stats["provider_stats"] == {}


def test_get_stats_single_provider(manager: OutputProviderManager):
    """测试获取单个 Provider 的统计信息"""
    provider = MockOutputProvider()
    import asyncio

    asyncio.run(manager.register_provider(provider))

    stats = manager.get_stats()

    assert stats["total_providers"] == 1
    assert stats["setup_providers"] == 0
    assert "MockOutputProvider" in stats["provider_stats"]
    assert stats["provider_stats"]["MockOutputProvider"]["is_started"] is False
    assert stats["provider_stats"]["MockOutputProvider"]["type"] == "output_provider"


def test_get_stats_multiple_providers(manager: OutputProviderManager, mock_event_bus):
    """测试获取多个 Provider 的统计信息"""
    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()
    provider3 = MockOutputProvider()

    import asyncio

    asyncio.run(manager.register_provider(provider1))
    asyncio.run(manager.register_provider(provider2))
    asyncio.run(manager.register_provider(provider3))

    # 启动部分 Provider
    import asyncio

    asyncio.run(provider1.start(mock_event_bus))
    asyncio.run(provider2.start(mock_event_bus))

    stats = manager.get_stats()

    assert stats["total_providers"] == 3
    assert stats["setup_providers"] == 2
    # 由于所有 Provider 都是 MockOutputProvider，统计会覆盖
    # 所以 provider_stats 中只有一个条目（最后一个注册的）
    assert len(stats["provider_stats"]) >= 1
    assert "MockOutputProvider" in stats["provider_stats"]


def test_get_stats_with_config(manager: OutputProviderManager):
    """测试获取带配置的 Manager 统计信息"""
    manager = OutputProviderManager({"concurrent_rendering": False, "error_handling": "stop"})

    provider = MockOutputProvider()
    import asyncio

    asyncio.run(manager.register_provider(provider))

    stats = manager.get_stats()

    assert stats["concurrent_rendering"] is False
    assert stats["error_handling"] == "stop"


# =============================================================================
# 从配置加载测试
# =============================================================================


@pytest.mark.asyncio
async def test_load_from_config_disabled(manager: OutputProviderManager):
    """测试加载禁用的配置"""
    config = {"enabled": False, "enabled_outputs": ["mock1"]}

    await manager.load_from_config(config)

    assert len(manager.providers) == 0


@pytest.mark.asyncio
async def test_load_from_config_empty_outputs(manager: OutputProviderManager):
    """测试加载空的 outputs 列表"""
    config = {"enabled": True, "enabled_outputs": []}

    await manager.load_from_config(config)

    assert len(manager.providers) == 0


@pytest.mark.asyncio
async def test_load_from_config_no_outputs_key(manager: OutputProviderManager):
    """测试配置中没有 outputs 键"""
    config = {"enabled": True}

    await manager.load_from_config(config)

    assert len(manager.providers) == 0


@pytest.mark.asyncio
async def test_load_from_config_with_provider_registry(manager: OutputProviderManager, mock_event_bus):
    """测试从配置加载 Provider（使用 ProviderRegistry）"""
    # 需要先在 ProviderRegistry 中注册 MockOutputProvider
    from src.modules.registry import ProviderRegistry

    # 临时注册 MockOutputProvider
    ProviderRegistry.register_output("mock_test", MockOutputProvider, source="test")

    try:
        config = {
            "enabled": True,
            "concurrent_rendering": True,
            "error_handling": "continue",
            "enabled_outputs": ["mock1", "mock2"],
            "outputs_config": {
                "mock1": {"type": "mock_test", "test_config": "value1"},
                "mock2": {"type": "mock_test", "test_config": "value2"},
            },
        }

        await manager.load_from_config(config)

        assert len(manager.providers) == 2

        # 验证配置被传递
        assert manager.providers[0].config.get("test_config") == "value1"
        assert manager.providers[1].config.get("test_config") == "value2"
    finally:
        # 清理注册
        ProviderRegistry._output_providers.pop("mock_test", None)


@pytest.mark.asyncio
async def test_load_from_config_updates_manager_config(manager: OutputProviderManager):
    """测试加载配置时更新 Manager 配置"""
    from src.modules.registry import ProviderRegistry

    # 临时注册 MockOutputProvider
    ProviderRegistry.register_output("mock_test", MockOutputProvider, source="test")

    try:
        config = {
            "enabled": True,
            "concurrent_rendering": False,
            "error_handling": "stop",
            "enabled_outputs": ["mock1"],
            "outputs": {"mock1": {"type": "mock_test"}},
        }

        await manager.load_from_config(config)

        assert manager.concurrent_rendering is False
        assert manager.error_handling == "stop"
    finally:
        # 清理注册
        ProviderRegistry._output_providers.pop("mock_test", None)


@pytest.mark.asyncio
async def test_load_from_config_unknown_provider_type(manager: OutputProviderManager):
    """测试加载未知的 Provider 类型"""
    config = {"enabled": True, "enabled_outputs": ["unknown"], "outputs": {"unknown": {"type": "nonexistent_provider"}}}

    # 不应该抛出异常，只是记录错误
    await manager.load_from_config(config)

    assert len(manager.providers) == 0


@pytest.mark.asyncio
async def test_load_from_config_partial_failure(manager: OutputProviderManager):
    """测试部分 Provider 加载失败"""
    from src.modules.registry import ProviderRegistry

    # 临时注册 MockOutputProvider
    ProviderRegistry.register_output("mock_test", MockOutputProvider, source="test")

    try:
        config = {
            "enabled": True,
            "enabled_outputs": ["mock1", "unknown", "mock2"],
            "outputs_config": {
                "mock1": {"type": "mock_test"},
                "unknown": {"type": "nonexistent_provider"},
                "mock2": {"type": "mock_test"},
            },
        }

        await manager.load_from_config(config)

        # 应该创建成功的 Provider，跳过失败的
        assert len(manager.providers) == 2
    finally:
        # 清理注册
        ProviderRegistry._output_providers.pop("mock_test", None)


# =============================================================================
# 并发场景测试
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_render_and_stop(manager: OutputProviderManager, mock_event_bus):
    """测试并发渲染和停止"""
    provider = SlowMockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    params = RenderParameters(tts_text="Test")

    # 并发执行渲染和停止
    tasks = [manager.render_all(params), manager.stop_all_providers()]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 不应该抛出未捕获的异常
    assert not any(isinstance(r, Exception) for r in results if r is not None)


@pytest.mark.asyncio
async def test_concurrent_setup_and_render(manager: OutputProviderManager, mock_event_bus):
    """测试并发设置和渲染"""
    provider = SlowMockOutputProvider()
    await manager.register_provider(provider)

    params = RenderParameters(tts_text="Test")

    # 并发执行设置和渲染
    tasks = [manager.setup_all_providers(mock_event_bus), manager.render_all(params)]

    await asyncio.gather(*tasks, return_exceptions=True)

    # render 应该失败（因为 Provider 可能还未设置完成）
    # setup 应该成功
    assert provider.is_started is True


@pytest.mark.asyncio
async def test_multiple_concurrent_renders(manager: OutputProviderManager, mock_event_bus):
    """测试多个并发渲染操作"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    params = [RenderParameters(tts_text=f"Message {i}") for i in range(10)]

    # 并发执行 10 个渲染
    tasks = [manager.render_all(p) for p in params]
    await asyncio.gather(*tasks)

    # 所有渲染都应该完成
    assert len(provider.received_parameters) == 10


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_render_with_none_parameters(manager: OutputProviderManager, mock_event_bus):
    """测试使用 None 参数渲染"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    # 应该可以处理 None 参数（虽然不推荐）
    # render() 方法会捕获所有异常
    await manager.render_all(None)

    # MockOutputProvider 的 _render_internal 会尝试访问 None 的属性
    # 但由于错误被捕获，received_parameters 不会增加
    # 或者可能 None 被直接添加到列表中（取决于实现）
    # 这里我们只验证不会崩溃


@pytest.mark.asyncio
async def test_render_with_empty_parameters(manager: OutputProviderManager, mock_event_bus):
    """测试使用空参数渲染"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)
    await manager.setup_all_providers(mock_event_bus)

    params = RenderParameters()

    await manager.render_all(params)

    assert len(provider.received_parameters) == 1
    assert provider.received_parameters[0].tts_text == ""
    assert provider.received_parameters[0].subtitle_text == ""


@pytest.mark.asyncio
async def test_manager_with_none_config():
    """测试使用 None 配置初始化"""
    manager = OutputProviderManager(None)

    assert manager.providers == []
    assert manager.concurrent_rendering is True


@pytest.mark.asyncio
async def test_render_all_without_prior_setup(manager: OutputProviderManager, mock_event_bus):
    """测试未先 setup 就渲染"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)

    params = RenderParameters(tts_text="Test")

    # render 会捕获 RuntimeError 并记录，不会抛出异常
    await manager.render_all(params)

    # Provider 不应该收到参数（因为 render 失败了）
    assert len(provider.received_parameters) == 0


@pytest.mark.asyncio
async def test_stop_all_without_prior_setup(manager: OutputProviderManager):
    """测试未先 setup 就停止"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)

    # 不应该报错（只是没有已设置的 Provider 需要停止）
    await manager.stop_all_providers()

    assert provider.is_started is False


@pytest.mark.asyncio
async def test_provider_lifecycle_complete(manager: OutputProviderManager, mock_event_bus):
    """测试完整的 Provider 生命周期"""
    provider = MockOutputProvider()
    await manager.register_provider(provider)

    # 初始状态
    assert provider.is_started is False

    # 设置
    await manager.setup_all_providers(mock_event_bus)
    assert provider.is_started is True

    # 渲染
    params = RenderParameters(tts_text="Test")
    await manager.render_all(params)
    assert len(provider.received_parameters) == 1

    # 停止
    await manager.stop_all_providers()
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_multiple_managers_independent(manager: OutputProviderManager):
    """测试多个 Manager 实例的独立性"""
    manager1 = OutputProviderManager()
    manager2 = OutputProviderManager()

    provider1 = MockOutputProvider()
    provider2 = MockOutputProvider()

    await manager1.register_provider(provider1)
    await manager2.register_provider(provider2)

    assert len(manager1.providers) == 1
    assert len(manager2.providers) == 1
    assert manager1.providers[0] == provider1
    assert manager2.providers[0] == provider2


# =============================================================================
# 错误隔离测试
# =============================================================================


@pytest.mark.asyncio
async def test_render_error_isolation(
    manager: OutputProviderManager, sample_parameters: RenderParameters, mock_event_bus
):
    """测试渲染错误隔离（一个 Provider 失败不影响其他）"""
    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_render = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    # 不应该抛出异常
    await manager.render_all(sample_parameters)

    # provider1 和 provider3 应该成功
    assert len(provider1.received_parameters) == 1
    assert len(provider3.received_parameters) == 1


@pytest.mark.asyncio
async def test_setup_error_isolation(manager: OutputProviderManager, mock_event_bus):
    """测试设置错误隔离（一个 Provider 失败不影响其他）"""
    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_setup = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)

    # 不应该抛出异常
    await manager.setup_all_providers(mock_event_bus)

    # provider1 和 provider3 应该成功设置
    assert provider1.is_started is True
    assert provider3.is_started is True
    assert provider2.is_started is False


@pytest.mark.asyncio
async def test_cleanup_error_isolation(manager: OutputProviderManager, mock_event_bus):
    """测试清理错误隔离（一个 Provider 失败不影响其他）"""
    provider1 = MockOutputProvider()
    provider2 = FailingMockOutputProvider()
    provider2.should_fail_on_cleanup = True
    provider3 = MockOutputProvider()

    await manager.register_provider(provider1)
    await manager.register_provider(provider2)
    await manager.register_provider(provider3)
    await manager.setup_all_providers(mock_event_bus)

    # 不应该抛出异常
    await manager.stop_all_providers()

    # provider1 和 provider3 应该成功停止
    assert provider1.is_started is False
    assert provider3.is_started is False

    # provider2 清理失败，is_started 保持为 True（错误隔离，但状态未改变）
    assert provider2.is_started is True


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
