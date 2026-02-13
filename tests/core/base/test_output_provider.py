"""
OutputProvider 单元测试

测试 OutputProvider 抽象基类的所有核心功能：
- 抽象方法验证（_render_internal）
- start() 方法流程
- _render_internal() 抽象方法
- stop() 方法
- get_info() 方法
- 生命周期管理
- 事件驱动渲染

运行: uv run pytest tests/core/base/test_output_provider.py -v
"""

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.types import ActionType, EmotionType, Intent, IntentAction
from src.modules.types.base.output_provider import OutputProvider


# =============================================================================
# 测试用的 OutputProvider 实现
# =============================================================================


def create_test_intent(response_text: str = "测试响应") -> Intent:
    """创建测试用的 Intent"""
    return Intent(
        original_text="测试输入",
        response_text=response_text,
        actions=[IntentAction(type=ActionType.NONE)],
        emotion=EmotionType.NEUTRAL,
    )


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
        self.last_intent = None

    async def _start_internal(self):
        """模拟内部启动"""
        self.start_called = True

    async def _render_internal(self, intent: Intent):
        """模拟内部渲染逻辑"""
        self.render_count += 1
        self.last_intent = intent

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
def event_bus():
    """创建 EventBus 实例"""
    from src.modules.events.registry import EventRegistry, register_core_events

    # 确保核心事件已注册
    if not EventRegistry.is_registered(CoreEvents.OUTPUT_INTENT):
        register_core_events()

    return EventBus()


@pytest.fixture
def test_intent():
    """创建测试用的 Intent"""
    return create_test_intent("测试响应")


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
async def test_output_provider_start_basic(event_bus):
    """测试 start() 基本流程"""
    provider = MockOutputProvider({})

    await provider.start(event_bus=event_bus)

    assert provider.event_bus == event_bus
    assert provider.is_started is True
    assert provider.start_called is True


@pytest.mark.asyncio
async def test_output_provider_start_with_audio_stream_channel(event_bus):
    """测试 start() 接受 AudioStreamChannel"""
    provider = MockOutputProvider({})
    mock_audio_channel = object()

    await provider.start(event_bus=event_bus, audio_stream_channel=mock_audio_channel)

    # AudioStreamChannel 应该被存储
    assert provider.audio_stream_channel == mock_audio_channel


@pytest.mark.asyncio
async def test_output_provider_start_without_audio_stream_channel(event_bus):
    """测试 start() 不传 audio_stream_channel 时为 None"""
    provider = MockOutputProvider({})

    await provider.start(event_bus=event_bus)

    assert provider.audio_stream_channel is None


@pytest.mark.asyncio
async def test_output_provider_start_multiple_calls(event_bus):
    """测试 start() 可以多次调用"""
    provider = MockOutputProvider({})

    await provider.start(event_bus=event_bus)
    assert provider.is_started is True

    # 再次调用（不应该抛出异常）
    await provider.start(event_bus=event_bus)
    assert provider.is_started is True


# =============================================================================
# _render_internal() 方法测试（直接调用）
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_render_internal_basic(mock_provider, test_intent):
    """测试 _render_internal() 基本功能"""
    # 直接调用 _render_internal
    await mock_provider._render_internal(test_intent)

    assert mock_provider.render_count == 1
    assert mock_provider.last_intent == test_intent


@pytest.mark.asyncio
async def test_output_provider_render_internal_multiple_calls(mock_provider, test_intent):
    """测试 _render_internal() 可以多次调用"""
    # 多次调用
    await mock_provider._render_internal(test_intent)
    await mock_provider._render_internal(test_intent)
    await mock_provider._render_internal(test_intent)

    assert mock_provider.render_count == 3


@pytest.mark.asyncio
async def test_output_provider_render_internal_with_error(error_provider, test_intent):
    """测试 _render_internal() 抛出错误的情况"""
    with pytest.raises(ValueError) as exc_info:
        await error_provider._render_internal(test_intent)

    assert "模拟渲染错误" in str(exc_info.value)


# =============================================================================
# 事件驱动渲染测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_event_driven_render(event_bus, mock_provider, test_intent):
    """测试通过事件驱动渲染"""
    # 启动 Provider（会订阅 OUTPUT_INTENT 事件）
    await mock_provider.start(event_bus=event_bus)

    # 创建 IntentPayload 并发送事件
    payload = IntentPayload.from_intent(test_intent, "test_provider")
    await event_bus.emit(CoreEvents.OUTPUT_INTENT, payload, source="test")

    # 等待事件处理
    import asyncio

    await asyncio.sleep(0.1)

    # Provider 应该收到了 Intent
    assert mock_provider.render_count == 1
    assert mock_provider.last_intent.response_text == test_intent.response_text


@pytest.mark.asyncio
async def test_output_provider_event_driven_multiple_renders(event_bus, mock_provider, test_intent):
    """测试多次事件驱动渲染"""
    await mock_provider.start(event_bus=event_bus)

    # 发送多个事件
    for i in range(3):
        intent = create_test_intent(f"响应 {i}")
        payload = IntentPayload.from_intent(intent, "test_provider")
        await event_bus.emit(CoreEvents.OUTPUT_INTENT, payload, source="test")

    import asyncio

    await asyncio.sleep(0.1)

    assert mock_provider.render_count == 3


# =============================================================================
# stop() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_stop_basic(event_bus):
    """测试 stop() 基本功能"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=event_bus)

    assert provider.is_started is True

    await provider.stop()

    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_stop_idempotent(event_bus):
    """测试 stop() 可以多次调用（幂等）"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=event_bus)

    await provider.stop()
    await provider.stop()
    await provider.stop()

    # 不应该抛出异常
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_stop_after_render(event_bus, test_intent):
    """测试渲染后 stop() 正常工作"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=event_bus)
    await provider._render_internal(test_intent)

    assert provider.render_count == 1

    await provider.stop()

    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_stop_unsubscribes_from_events(event_bus, mock_provider, test_intent):
    """测试 stop() 后不再接收事件"""
    await mock_provider.start(event_bus=event_bus)

    # 发送事件，应该收到
    payload = IntentPayload.from_intent(test_intent, "test_provider")
    await event_bus.emit(CoreEvents.OUTPUT_INTENT, payload, source="test")

    import asyncio

    await asyncio.sleep(0.1)
    assert mock_provider.render_count == 1

    # 停止
    await mock_provider.stop()

    # 重置计数
    mock_provider.render_count = 0

    # 再次发送事件，不应该收到
    await event_bus.emit(CoreEvents.OUTPUT_INTENT, payload, source="test")
    await asyncio.sleep(0.1)

    assert mock_provider.render_count == 0


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
async def test_output_provider_get_info_after_start(event_bus):
    """测试 start() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=event_bus)

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is True
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_stop(event_bus):
    """测试 stop() 后 get_info() 返回正确的状态"""
    provider = MockOutputProvider({})
    await provider.start(event_bus=event_bus)
    await provider.stop()

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is False
    assert info["type"] == "output_provider"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_full_lifecycle(event_bus, test_intent):
    """测试 OutputProvider 完整生命周期"""
    provider = MockOutputProvider({})

    # 1. 初始化
    assert provider.is_started is False
    assert provider.start_called is False
    assert provider.stop_called is False
    assert provider.render_count == 0

    # 2. 启动
    await provider.start(event_bus=event_bus)
    assert provider.is_started is True
    assert provider.start_called is True

    # 3. 渲染
    await provider._render_internal(test_intent)
    assert provider.render_count == 1

    # 4. 停止
    await provider.stop()
    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_restart(event_bus, test_intent):
    """测试 OutputProvider 可以重新启动"""
    provider = MockOutputProvider({})

    # 第一次运行
    await provider.start(event_bus=event_bus)
    await provider._render_internal(test_intent)
    await provider.stop()

    assert provider.render_count == 1
    assert provider.is_started is False

    # 第二次运行
    await provider.start(event_bus=event_bus)
    await provider._render_internal(test_intent)
    await provider.stop()

    assert provider.render_count == 2


@pytest.mark.asyncio
async def test_output_provider_lifecycle_state_transitions(event_bus):
    """测试生命周期状态转换"""
    provider = MockOutputProvider({})

    # 初始状态
    info = provider.get_info()
    assert info["is_started"] is False

    # start 后
    await provider.start(event_bus=event_bus)
    info = provider.get_info()
    assert info["is_started"] is True

    # stop 后
    await provider.stop()
    info = provider.get_info()
    assert info["is_started"] is False

    # 再次 start
    await provider.start(event_bus=event_bus)
    info = provider.get_info()
    assert info["is_started"] is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_start_with_mock_event_bus():
    """测试 start() 使用 mock event_bus（没有 on 方法）"""
    provider = MockOutputProvider({})

    # 使用一个简单的 mock 对象，提供 on 方法
    class MockEventBus:
        def on(self, *args, **kwargs):
            pass

        def off(self, *args, **kwargs):
            pass

    mock_bus = MockEventBus()
    await provider.start(event_bus=mock_bus)

    assert provider.event_bus == mock_bus
    assert provider.is_started is True


@pytest.mark.asyncio
async def test_output_provider_with_empty_config(test_intent):
    """测试使用空配置的 OutputProvider"""
    provider = MockOutputProvider({})

    await provider._render_internal(test_intent)

    assert provider.render_count == 1
    assert provider.config == {}


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
