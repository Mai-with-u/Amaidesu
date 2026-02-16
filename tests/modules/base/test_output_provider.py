"""
OutputProvider 单元测试

测试 OutputProvider 抽象基类的所有核心功能：
- 抽象方法验证（execute）
- start() 方法流程
- execute() 抽象方法
- stop() 方法
- get_info() 方法
- 生命周期管理
- 事件驱动渲染

运行: uv run pytest tests/core/base/test_output_provider.py -v
"""

import pytest

from src.modules.di.context import ProviderContext
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

    def __init__(self, config: dict, context: ProviderContext = None, raise_error: bool = False):
        """
        初始化 Mock OutputProvider

        Args:
            config: Provider 配置
            context: Provider 上下文（必填）
            raise_error: 是否在 execute 时抛出错误
        """
        super().__init__(config, context)
        self.raise_error = raise_error
        self.init_called = False
        self.start_called = False
        self.cleanup_called = False
        self.stop_called = False
        self.execute_count = 0
        self.render_count = 0
        self.last_intent = None

    async def init(self):
        """模拟初始化"""
        self.init_called = True
        self.start_called = True

    async def execute(self, intent: Intent):
        """执行意图"""
        self.execute_count += 1
        self.render_count = self.execute_count  # 别名
        self.last_intent = intent

        if self.raise_error:
            raise ValueError("模拟渲染错误")

    async def cleanup(self):
        """清理资源"""
        self.cleanup_called = True
        self.stop_called = True


class IncompleteOutputProvider(OutputProvider):
    """不完整的 OutputProvider（未实现 execute）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def event_bus():
    """创建 EventBus 实例

    事件会在 Provider 订阅时自动注册，无需预先注册。
    """
    return EventBus()


@pytest.fixture
def mock_context(event_bus):
    """创建带有 event_bus 的 ProviderContext"""
    return ProviderContext(event_bus=event_bus)


@pytest.fixture
def mock_context_with_audio(event_bus):
    """创建带有 event_bus 和 audio_stream_channel 的 ProviderContext"""
    mock_audio_channel = object()
    return ProviderContext(event_bus=event_bus, audio_stream_channel=mock_audio_channel), mock_audio_channel


@pytest.fixture
def mock_provider(mock_context):
    """创建标准的 MockOutputProvider 实例"""
    return MockOutputProvider(config={"device": "test_device"}, context=mock_context)


@pytest.fixture
def error_provider(mock_context):
    """创建会抛出错误的 OutputProvider"""
    return MockOutputProvider(config={}, context=mock_context, raise_error=True)


@pytest.fixture
def test_intent():
    """创建测试用的 Intent"""
    return create_test_intent("测试响应")


# =============================================================================
# 实例化和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_initialization(mock_context, event_bus):
    """测试 OutputProvider 初始化"""
    config = {"device": "TTS", "voice": "female"}
    provider = MockOutputProvider(config, mock_context)

    assert provider.config == config
    assert provider.event_bus == event_bus
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_default_config(mock_context):
    """测试使用默认配置初始化"""
    provider = MockOutputProvider({}, mock_context)

    assert provider.config == {}
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_requires_context():
    """测试 OutputProvider 必须接收 context 参数"""
    with pytest.raises(ValueError) as exc_info:
        MockOutputProvider({}, context=None)

    assert "必须接收 context 参数" in str(exc_info.value)


# =============================================================================
# 抽象方法验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_abstract_method_not_implemented():
    """测试未实现抽象方法的子类无法实例化"""
    with pytest.raises(TypeError):
        # IncompleteOutputProvider 未实现 execute
        _ = IncompleteOutputProvider({})


# =============================================================================
# start() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_start_basic(mock_provider):
    """测试 start() 基本流程"""
    await mock_provider.start()

    assert mock_provider.is_started is True
    assert mock_provider.start_called is True


@pytest.mark.asyncio
async def test_output_provider_start_with_audio_stream_channel(mock_context_with_audio):
    """测试 ProviderContext 中的 AudioStreamChannel"""
    context, mock_audio_channel = mock_context_with_audio
    provider = MockOutputProvider({}, context)

    await provider.start()

    # AudioStreamChannel 应该从 context 获取
    assert provider.audio_stream_channel == mock_audio_channel


@pytest.mark.asyncio
async def test_output_provider_start_without_audio_stream_channel(mock_context):
    """测试没有 audio_stream_channel 时为 None"""
    provider = MockOutputProvider({}, mock_context)

    await provider.start()

    assert provider.audio_stream_channel is None


@pytest.mark.asyncio
async def test_output_provider_start_multiple_calls(mock_provider):
    """测试 start() 可以多次调用"""
    await mock_provider.start()
    assert mock_provider.is_started is True

    # 再次调用（不应该抛出异常）
    await mock_provider.start()
    assert mock_provider.is_started is True


# =============================================================================
# execute() 方法测试（直接调用）
# =============================================================================


@pytest.mark.asyncio
async def test_output_providerexecute_basic(mock_provider, test_intent):
    """测试 execute() 基本功能"""
    # 直接调用 execute
    await mock_provider.execute(test_intent)

    assert mock_provider.render_count == 1
    assert mock_provider.last_intent == test_intent


@pytest.mark.asyncio
async def test_output_providerexecute_multiple_calls(mock_provider, test_intent):
    """测试 execute() 可以多次调用"""
    # 多次调用
    await mock_provider.execute(test_intent)
    await mock_provider.execute(test_intent)
    await mock_provider.execute(test_intent)

    assert mock_provider.render_count == 3


@pytest.mark.asyncio
async def test_output_providerexecute_with_error(error_provider, test_intent):
    """测试 execute() 抛出错误的情况"""
    with pytest.raises(ValueError) as exc_info:
        await error_provider.execute(test_intent)

    assert "模拟渲染错误" in str(exc_info.value)


# =============================================================================
# 事件驱动渲染测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_event_driven_render(mock_provider, test_intent, event_bus):
    """测试通过事件驱动渲染"""
    # 启动 Provider（会订阅 OUTPUT_INTENT 事件）
    await mock_provider.start()

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
async def test_output_provider_event_driven_multiple_renders(mock_provider, event_bus):
    """测试多次事件驱动渲染"""
    await mock_provider.start()

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
async def test_output_provider_stop_basic(mock_provider):
    """测试 stop() 基本功能"""
    await mock_provider.start()

    assert mock_provider.is_started is True

    await mock_provider.stop()

    assert mock_provider.is_started is False
    assert mock_provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_stop_idempotent(mock_provider):
    """测试 stop() 可以多次调用（幂等）"""
    await mock_provider.start()

    await mock_provider.stop()
    await mock_provider.stop()
    await mock_provider.stop()

    # 不应该抛出异常
    assert mock_provider.is_started is False


@pytest.mark.asyncio
async def test_output_provider_stop_after_render(mock_provider, test_intent):
    """测试渲染后 stop() 正常工作"""
    await mock_provider.start()
    await mock_provider.execute(test_intent)

    assert mock_provider.render_count == 1

    await mock_provider.stop()

    assert mock_provider.is_started is False
    assert mock_provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_stop_unsubscribes_from_events(mock_provider, test_intent, event_bus):
    """测试 stop() 后不再接收事件"""
    await mock_provider.start()

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
async def test_output_provider_get_info_basic(mock_context):
    """测试 get_info() 返回基本信息"""
    provider = MockOutputProvider({}, mock_context)

    info = provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is False
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_start(mock_provider):
    """测试 start() 后 get_info() 返回正确的状态"""
    await mock_provider.start()

    info = mock_provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is True
    assert info["type"] == "output_provider"


@pytest.mark.asyncio
async def test_output_provider_get_info_after_stop(mock_provider):
    """测试 stop() 后 get_info() 返回正确的状态"""
    await mock_provider.start()
    await mock_provider.stop()

    info = mock_provider.get_info()

    assert info["name"] == "MockOutputProvider"
    assert info["is_started"] is False
    assert info["type"] == "output_provider"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_full_lifecycle(mock_provider, test_intent):
    """测试 OutputProvider 完整生命周期"""
    # 1. 初始化
    assert mock_provider.is_started is False
    assert mock_provider.start_called is False
    assert mock_provider.stop_called is False
    assert mock_provider.render_count == 0

    # 2. 启动
    await mock_provider.start()
    assert mock_provider.is_started is True
    assert mock_provider.start_called is True

    # 3. 渲染
    await mock_provider.execute(test_intent)
    assert mock_provider.render_count == 1

    # 4. 停止
    await mock_provider.stop()
    assert mock_provider.is_started is False
    assert mock_provider.stop_called is True


@pytest.mark.asyncio
async def test_output_provider_restart(mock_provider, test_intent):
    """测试 OutputProvider 可以重新启动"""
    # 第一次运行
    await mock_provider.start()
    await mock_provider.execute(test_intent)
    await mock_provider.stop()

    assert mock_provider.render_count == 1
    assert mock_provider.is_started is False

    # 第二次运行
    await mock_provider.start()
    await mock_provider.execute(test_intent)
    await mock_provider.stop()

    assert mock_provider.render_count == 2


@pytest.mark.asyncio
async def test_output_provider_lifecycle_state_transitions(mock_provider):
    """测试生命周期状态转换"""
    # 初始状态
    info = mock_provider.get_info()
    assert info["is_started"] is False

    # start 后
    await mock_provider.start()
    info = mock_provider.get_info()
    assert info["is_started"] is True

    # stop 后
    await mock_provider.stop()
    info = mock_provider.get_info()
    assert info["is_started"] is False

    # 再次 start
    await mock_provider.start()
    info = mock_provider.get_info()
    assert info["is_started"] is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_output_provider_start_with_mock_event_bus():
    """测试 start() 使用 mock event_bus（没有 on 方法）"""

    # 使用一个简单的 mock 对象，提供 on 方法
    class MockEventBus:
        def on(self, *args, **kwargs):
            pass

        def off(self, *args, **kwargs):
            pass

    mock_bus = MockEventBus()
    context = ProviderContext(event_bus=mock_bus)
    provider = MockOutputProvider({}, context)

    await provider.start()

    assert provider.event_bus == mock_bus
    assert provider.is_started is True


@pytest.mark.asyncio
async def test_output_provider_with_empty_config(mock_context, test_intent):
    """测试使用空配置的 OutputProvider"""
    provider = MockOutputProvider({}, mock_context)

    await provider.execute(test_intent)

    assert provider.render_count == 1
    assert provider.config == {}


@pytest.mark.asyncio
async def test_output_provider_context_property_access(event_bus):
    """测试通过 context 属性访问依赖"""
    mock_audio_channel = object()
    context = ProviderContext(event_bus=event_bus, audio_stream_channel=mock_audio_channel)
    provider = MockOutputProvider({}, context)

    # event_bus 和 audio_stream_channel 应该从 context 获取
    assert provider.event_bus == event_bus
    assert provider.audio_stream_channel == mock_audio_channel
    assert provider.context == context


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
