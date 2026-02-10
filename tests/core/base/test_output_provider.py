"""
OutputProvider 单元测试

测试 OutputProvider 抽象基类的所有核心功能：
- 抽象方法验证（_render_internal）
- setup() 方法流程
- render() 方法（未 setup 时抛出 RuntimeError）
- _render_internal() 抽象方法
- cleanup() 方法
- get_info() 方法
- 生命周期管理

运行: uv run pytest tests/core/base/test_output_provider.py -v
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from src.core.base.output_provider import OutputProvider

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
        self.setup_called = False
        self.cleanup_called = False
        self.render_count = 0
        self.last_parameters = None

    async def _setup_internal(self):
        """模拟内部设置"""
        self.setup_called = True

    async def _render_internal(self, parameters: "RenderParameters"):
        """模拟内部渲染逻辑"""
        self.render_count += 1
        self.last_parameters = parameters

        if self.raise_error:
            raise ValueError("模拟渲染错误")

    async def _cleanup_internal(self):
        """模拟内部清理"""
        self.cleanup_called = True
        self.is_setup = False


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
    assert provider.is_setup is False


@pytest.mark.asyncio
async def test_output_provider_default_config():
    """测试使用默认配置初始化"""
    provider = MockOutputProvider({})

    assert provider.config == {}
    assert provider.is_setup is False
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
# setup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_setup_basic():
    """测试 setup() 基本流程"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)

    assert provider.event_bus == mock_event_bus
    assert provider.is_setup is True
    assert provider.setup_called is True


@pytest.mark.asyncio
async def test_output_provider_setup_with_dependencies():
    """测试 setup() 接受依赖注入"""
    provider = MockOutputProvider({})
    mock_event_bus = object()
    dependencies = {"audio_service": "mock_audio", "cache": "mock_cache"}

    await provider.setup(event_bus=mock_event_bus, dependencies=dependencies)

    # 依赖应该被存储
    assert hasattr(provider, "_dependencies")
    assert provider._dependencies == dependencies


@pytest.mark.asyncio
async def test_output_provider_setup_without_dependencies():
    """测试 setup() 不传 dependencies 时使用空字典"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)

    assert hasattr(provider, "_dependencies")
    assert provider._dependencies == {}


@pytest.mark.asyncio
async def test_output_provider_setup_multiple_calls():
    """测试 setup() 可以多次调用"""
    provider = MockOutputProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)
    assert provider.is_setup is True

    # 再次调用（不应该抛出异常）
    await provider.setup(event_bus=mock_event_bus)
    assert provider.is_setup is True


# =============================================================================
# render() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_render_basic(mock_provider, mock_render_parameters):
    """测试 render() 基本功能"""
    # 先 setup
    await mock_provider.setup(event_bus=object())

    # 调用 render
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 1
    assert mock_provider.last_parameters == mock_render_parameters


@pytest.mark.asyncio
async def test_output_provider_render_multiple_calls(mock_provider, mock_render_parameters):
    """测试 render() 可以多次调用"""
    await mock_provider.setup(event_bus=object())

    # 多次调用
    await mock_provider.render(mock_render_parameters)
    await mock_provider.render(mock_render_parameters)
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 3


@pytest.mark.asyncio
async def test_output_provider_render_before_setup_raises_error(mock_provider, mock_render_parameters):
    """测试未 setup 时调用 render() 抛出 RuntimeError"""
    # 不调用 setup，直接 render
    with pytest.raises(RuntimeError) as exc_info:
        await mock_provider.render(mock_render_parameters)

    assert "not setup" in str(exc_info.value).lower()
    assert "call setup() first" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_output_provider_render_with_error(error_provider, mock_render_parameters):
    """测试 render() 抛出错误的情况"""
    await error_provider.setup(event_bus=object())

    with pytest.raises(ValueError) as exc_info:
        await error_provider.render(mock_render_parameters)

    assert "模拟渲染错误" in str(exc_info.value)


# =============================================================================
# cleanup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_cleanup_basic():
    """测试 cleanup() 基本功能"""
    provider = MockOutputProvider({})
    await provider.setup(event_bus=object())

    assert provider.is_setup is True

    await provider.cleanup()

    assert provider.is_setup is False
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_output_provider_cleanup_idempotent():
    """测试 cleanup() 可以多次调用（幂等）"""
    provider = MockOutputProvider({})
    await provider.setup(event_bus=object())

    await provider.cleanup()
    await provider.cleanup()
    await provider.cleanup()

    # 不应该抛出异常
    assert provider.is_setup is False


@pytest.mark.asyncio
async def test_output_provider_cleanup_after_render(mock_provider, mock_render_parameters):
    """测试 render() 后 cleanup() 正常工作"""
    await mock_provider.setup(event_bus=object())
    await mock_provider.render(mock_render_parameters)

    assert mock_provider.render_count == 1

    await mock_provider.cleanup()

    assert mock_provider.is_setup is False
    assert mock_provider.cleanup_called is True


# =============================================================================
# get_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_get_info_basic():
    """测试 get_info() 返回基本信息"""
    provider = MockOutputProvider({})

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_setup"] is False
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_setup():
    """测试 setup() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.setup(event_bus=object())

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_setup"] is True
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_cleanup():
    """测试 cleanup() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.setup(event_bus=object())
    await provider.cleanup()

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_setup"] is False
    assert info["type"] == "output_provider"


# =============================================================================
# get_registration_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_get_registration_info_not_implemented():
    """测试 get_registration_info() 默认抛出 NotImplementedError"""
    provider = MockOutputProvider({})

    with pytest.raises(NotImplementedError) as exc_info:
        provider.get_registration_info()

    assert "必须实现 get_registration_info()" in str(exc_info.value)


@pytest.mark.asyncio
async def test_output_provider_get_registration_info_override():
    """测试子类可以重写 get_registration_info()"""

    class RegisteredOutputProvider(MockOutputProvider):
        """实现了注册方法的 Provider"""

        @classmethod
        def get_registration_info(cls):
            return {
                "layer": "output",
                "name": "mock_output_registered",
                "class": cls,
                "source": "test",
            }

    info = RegisteredOutputProvider.get_registration_info()

    assert info["layer"] == "output"
    assert info["name"] == "mock_output_registered"
    assert info["class"] == RegisteredOutputProvider
    assert info["source"] == "test"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_full_lifecycle(mock_render_parameters):
    """测试 OutputProvider 完整生命周期"""
    provider = MockOutputProvider({})

    # 1. 初始化
    assert provider.is_setup is False
    assert provider.setup_called is False
    assert provider.cleanup_called is False
    assert provider.render_count == 0

    # 2. 设置
    await provider.setup(event_bus=object())
    assert provider.is_setup is True
    assert provider.setup_called is True

    # 3. 渲染
    await provider.render(mock_render_parameters)
    assert provider.render_count == 1

    # 4. 清理
    await provider.cleanup()
    assert provider.is_setup is False
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_output_provider_restart(mock_render_parameters):
    """测试 OutputProvider 可以重新启动"""
    provider = MockOutputProvider({})

    # 第一次运行
    await provider.setup(event_bus=object())
    await provider.render(mock_render_parameters)
    await provider.cleanup()

    assert provider.render_count == 1
    assert provider.is_setup is False

    # 第二次运行
    await provider.setup(event_bus=object())
    await provider.render(mock_render_parameters)
    await provider.cleanup()

    assert provider.render_count == 2


@pytest.mark.asyncio
async def test_output_provider_lifecycle_state_transitions(mock_render_parameters):
    """测试生命周期状态转换"""
    provider = MockOutputProvider({})

    # 初始状态
    info = provider.get_info()
    assert info["is_setup"] is False

    # setup 后
    await provider.setup(event_bus=object())
    info = provider.get_info()
    assert info["is_setup"] is True

    # cleanup 后
    await provider.cleanup()
    info = provider.get_info()
    assert info["is_setup"] is False

    # 再次 setup
    await provider.setup(event_bus=object())
    info = provider.get_info()
    assert info["is_setup"] is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_setup_with_none_event_bus():
    """测试 setup() 传入 None event_bus"""
    provider = MockOutputProvider({})

    await provider.setup(event_bus=None)

    assert provider.event_bus is None
    assert provider.is_setup is True


@pytest.mark.asyncio
async def test_output_provider_with_empty_config(mock_render_parameters):
    """测试使用空配置的 OutputProvider"""
    provider = MockOutputProvider({})
    await provider.setup(event_bus=object())

    await provider.render(mock_render_parameters)

    assert provider.render_count == 1
    assert provider.config == {}


@pytest.mark.asyncio
async def test_output_provider_render_after_cleanup(mock_provider, mock_render_parameters):
    """测试 cleanup() 后 render() 抛出错误"""
    await mock_provider.setup(event_bus=object())
    await mock_provider.render(mock_render_parameters)
    await mock_provider.cleanup()

    # cleanup 后 render 应该抛出错误
    with pytest.raises(RuntimeError) as exc_info:
        await mock_provider.render(mock_render_parameters)

    assert "not setup" in str(exc_info.value).lower()


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
