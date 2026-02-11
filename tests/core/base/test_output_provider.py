"""
OutputProvider 单元测试

测试 OutputProvider 抽象基类的所有核心功能：
- 抽象方法验证（_render_internal）
- start() 方法流程
- render() 方法（未 start 时抛出 RuntimeError）
- _render_internal() 抽象方法
- stop() 方法
- get_info() 方法
- 生命周期管理

运行: uv run pytest tests/core/base/test_output_provider.py -v
"""

from typing import TYPE_CHECKING

import pytest

from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.domains.output.parameters.render_parameters import RenderParameters


# =============================================================================
# 测试用的 OutputProvider 实现
# =============================================================================


class MockOutputProvider(OutputProvider):
    """模拟的 OutputProvider 实现（用于测试）"""

    def __init__(self, config: dict, raise_error: bool = False):
        """
        初始化 Mock OutputProvider

        Args:
            config: Provider 配置
            raise_error: 是否在 render 时抛出错误
        """
        super().__init__(config)
        self.raise_error = raise_error
        self.start_called = False
        self.stop_called = False
        self.render_count = 0
        self.last_parameters = None

    async def _start_internal(self):
        """模拟内部启动"""
        self.start_called = True

    async def _render_internal(self, parameters: "RenderParameters"):
        """模拟内部渲染逻辑"""
        self.render_count += 1
        self.last_parameters = parameters

        if self.raise_error:
            raise ValueError("模拟渲染错误")

    async def _stop_internal(self):
        """模拟内部停止"""
        self.stop_called = True


class IncompleteOutputProvider(OutputProvider):
    """不完整的 OutputProvider（未实现 _render_internal）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def mock_provider():
    """创建标准的 MockOutputProvider 实例"""
    return MockOutputProvider(config={"device": "test_device"})


@pytest.fixture
def error_provider():
    """创建会抛出错误的 OutputProvider"""
    return MockOutputProvider(config={}, raise_error=True)


@pytest.fixture
def mock_render_parameters():
    """创建模拟的 RenderParameters（使用字典）"""
    return {
        "text": "测试文本",
        "emotion": "happy",
        "action": "wave",
        "metadata": {"test": "data"},
    }


# =============================================================================
# 实例化和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_initialization():
    """测试 OutputProvider 初始化"""
    config = {"device": "TTS", "voice": "female"}
    provider = MockOutputProvider(config)

    assert provider.config == config
    assert provider.event_bus is None
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_default_config():
    """测试使用默认配置初始化"""
    provider = MockOutputProvider({})

    assert provider.config == {}
    assert provider.is_started is False
    assert provider.event_bus is None


# =============================================================================
# 抽象方法验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_abstract_method_not_implemented():
    """测试未实现抽象方法的子类无法实例化"""
    with pytest.raises(TypeError):
        # IncompleteOutputProvider 未实现 _render_internal
        _ = IncompleteOutputProvider({})


# =============================================================================
# start() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_start_basic():
    """测试 start() 基本流程"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.start(event_bus=mock_event_bus)

    assert provider.event_bus == mock_event_bus
    assert provider.is_started is True
    assert provider.start_called is True


@pytest.mark.asyncio
async def test_output_provider_start_with_audio_stream_channel():
    """测试 start() 接受 AudioStreamChannel"""
    provider = MockOutputProvider({})
    mock_event_bus = object()
    mock_audio_channel = object()

    await provider.start(event_bus=mock_event_bus, audio_stream_channel=mock_audio_channel)

    # AudioStreamChannel 应该被存储
    assert provider.audio_stream_channel == mock_audio_channel


@pytest.mark.asyncio
async def test_output_provider_start_without_audio_stream_channel():
    """测试 start() 不传 audio_stream_channel 时为 None"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.start(event_bus=mock_event_bus)

    assert provider.audio_stream_channel is None


@pytest.mark.asyncio
async def test_output_provider_start_multiple_calls():
    """测试 start() 可以多次调用"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.start(event_bus=mock_event_bus)
    assert provider.is_started is True

    # 再次调用（不应该抛出异常）
    await provider.start(event_bus=mock_event_bus)
    assert provider.is_started is True


# =============================================================================
# render() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_render_basic(mock_provider, mock_render_parameters):
    """测试 render() 基本功能"""
    # 先 start
    await mock_provider.start(event_bus=object())

    # 调用 render
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 1
    assert mock_provider.last_parameters == mock_render_parameters


@pytest.mark.asyncio
async def test_output_provider_render_multiple_calls(mock_provider, mock_render_parameters):
    """测试 render() 可以多次调用"""
    await mock_provider.start(event_bus=object())

    # 多次调用
    await mock_provider.render(mock_render_parameters)
    await mock_provider.render(mock_render_parameters)
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 3


@pytest.mark.asyncio
async def test_output_provider_render_before_start_raises_error(mock_provider, mock_render_parameters):
    """测试未 start 时调用 render() 抛出 RuntimeError"""
    # 不调用 start，直接 render
    with pytest.raises(RuntimeError) as exc_info:
        await mock_provider.render(mock_render_parameters)

    assert "not started" in str(exc_info.value).lower()
    assert "call start() first" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_output_provider_render_with_error(error_provider, mock_render_parameters):
    """测试 render() 抛出错误的情况"""
    await error_provider.start(event_bus=object())

    with pytest.raises(ValueError) as exc_info:
        await error_provider.render(mock_render_parameters)

    assert "模拟渲染错误" in str(exc_info.value)


# =============================================================================
# stop() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_stop_basic():
    """测试 stop() 基本功能"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=object())

    assert provider.is_started is True

    await provider.stop()

    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_stop_idempotent():
    """测试 stop() 可以多次调用（幂等）"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=object())

    await provider.stop()
    await provider.stop()
    await provider.stop()

    # 不应该抛出异常
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_stop_after_render(mock_provider, mock_render_parameters):
    """测试 render() 后 stop() 正常工作"""
    await mock_provider.start(event_bus=object())
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 1

    await mock_provider.stop()

    assert mock_provider.is_started is False
    assert mock_provider.stop_called is True


# =============================================================================
# get_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_get_info_basic():
    """测试 get_info() 返回基本信息"""
    provider = MockOutputProvider({})

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is False
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_start():
    """测试 start() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=object())

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is True
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_stop():
    """测试 stop() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=object())
    await provider.stop()

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is False
    assert info["type"] == "output_provider"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_full_lifecycle(mock_render_parameters):
    """测试 OutputProvider 完整生命周期"""
    provider = MockOutputProvider({})

    # 1. 初始化
    assert provider.is_started is False
    assert provider.start_called is False
    assert provider.stop_called is False
    assert provider.render_count == 0

    # 2. 启动
    await provider.start(event_bus=object())
    assert provider.is_started is True
    assert provider.start_called is True

    # 3. 渲染
    await provider.render(mock_render_parameters)
    assert provider.render_count == 1

    # 4. 停止
    await provider.stop()
    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_restart(mock_render_parameters):
    """测试 OutputProvider 可以重新启动"""
    provider = MockOutputProvider({})

    # 第一次运行
    await provider.start(event_bus=object())
    await provider.render(mock_render_parameters)
    await provider.stop()

    assert provider.render_count == 1
    assert provider.is_started is False

    # 第二次运行
    await provider.start(event_bus=object())
    await provider.render(mock_render_parameters)
    await provider.stop()

    assert provider.render_count == 2


@pytest.mark.asyncio
async def test_output_provider_lifecycle_state_transitions(mock_render_parameters):
    """测试生命周期状态转换"""
    provider = MockOutputProvider({})

    # 初始状态
    info = provider.get_info()
    assert info["is_started"] is False

    # start 后
    await provider.start(event_bus=object())
    info = provider.get_info()
    assert info["is_started"] is True

    # stop 后
    await provider.stop()
    info = provider.get_info()
    assert info["is_started"] is False

    # 再次 start
    await provider.start(event_bus=object())
    info = provider.get_info()
    assert info["is_started"] is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_start_with_none_event_bus():
    """测试 start() 传入 None event_bus"""
    provider = MockOutputProvider({})

    await provider.start(event_bus=None)

    assert provider.event_bus is None
    assert provider.is_started is True


@pytest.mark.asyncio
async def test_output_provider_with_empty_config(mock_render_parameters):
    """测试使用空配置的 OutputProvider"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=object())

    await provider.render(mock_render_parameters)

    assert provider.render_count == 1
    assert provider.config == {}


@pytest.mark.asyncio
async def test_output_provider_render_after_stop(mock_provider, mock_render_parameters):
    """测试 stop() 后 render() 抛出错误"""
    await mock_provider.start(event_bus=object())
    await mock_provider.render(mock_render_parameters)
    await mock_provider.stop()

    # stop 后 render 应该抛出错误
    with pytest.raises(RuntimeError) as exc_info:
        await mock_provider.render(mock_render_parameters)

    assert "not started" in str(exc_info.value).lower()


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
