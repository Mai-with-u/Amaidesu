"""
DecisionProvider 单元测试

测试 DecisionProvider 抽象基类的所有核心功能：
- 抽象方法验证（decide）
- start() 方法流程
- decide() 抽象方法
- stop() 方法
- get_registration_info() NotImplementedError
- 生命周期管理

运行: uv run pytest tests/core/base/test_decision_provider.py -v
"""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from src.modules.di.context import ProviderContext
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.normalized_message import NormalizedMessage

if TYPE_CHECKING:
    from src.modules.types import Intent


# =============================================================================
# 测试用的 DecisionProvider 实现
# =============================================================================


class MockDecisionProvider(DecisionProvider):
    """模拟的 DecisionProvider 实现（用于测试）"""

    def __init__(self, config: dict, context: ProviderContext = None, raise_error: bool = False):
        """
        初始化 Mock DecisionProvider

        Args:
            config: Provider 配置
            context: Provider 上下文（必填）
            raise_error: 是否在 decide 时抛出错误
        """
        super().__init__(config, context)
        self.raise_error = raise_error
        self.start_called = False
        self.stop_called = False
        self.decide_count = 0

    async def init(self):
        """模拟初始化"""
        self.start_called = True

    async def decide(self, message: NormalizedMessage) -> "Intent":
        """模拟决策逻辑"""
        self.decide_count += 1

        if self.raise_error:
            raise ValueError("模拟决策错误")

        # 返回模拟的 Intent（使用字典模拟）
        return {
            "action": "response",
            "text": f"回复: {message.text}",
            "emotion": "neutral",
            "metadata": {"decide_count": self.decide_count},
        }

    async def cleanup(self):
        """模拟清理"""
        self.stop_called = True


class IncompleteDecisionProvider(DecisionProvider):
    """不完整的 DecisionProvider（未实现 decide）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def mock_context():
    """创建用于测试的 Mock ProviderContext"""
    return ProviderContext()


@pytest.fixture
def mock_context_with_event_bus():
    """创建带有 mock event_bus 的 ProviderContext"""
    mock_event_bus = MagicMock()
    return ProviderContext(event_bus=mock_event_bus), mock_event_bus


@pytest.fixture
def mock_provider(mock_context):
    """创建标准的 MockDecisionProvider 实例"""
    return MockDecisionProvider(config={"model": "test"}, context=mock_context)


@pytest.fixture
def error_provider(mock_context):
    """创建会抛出错误的 DecisionProvider"""
    return MockDecisionProvider(config={}, context=mock_context, raise_error=True)


@pytest.fixture
def mock_normalized_message():
    """创建模拟的 NormalizedMessage"""

    class MockContent:
        def get_display_text(self) -> str:
            return "测试消息"

        def get_user_id(self) -> str:
            return "user123"

        type = "text"

    return NormalizedMessage(
        text="测试消息",
        content=MockContent(),
        source="test",
        data_type="text",
        importance=0.5,
    )


# =============================================================================
# 实例化和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_initialization(mock_context_with_event_bus):
    """测试 DecisionProvider 初始化"""
    context, mock_event_bus = mock_context_with_event_bus
    config = {"model": "gpt-4", "api_key": "test_key"}
    provider = MockDecisionProvider(config, context)

    assert provider.config == config
    assert provider.event_bus == mock_event_bus
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_decision_provider_default_config(mock_context):
    """测试使用默认配置初始化"""
    provider = MockDecisionProvider({}, mock_context)

    assert provider.config == {}
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_decision_provider_requires_context():
    """测试 DecisionProvider 必须接收 context 参数"""
    with pytest.raises(ValueError) as exc_info:
        MockDecisionProvider({}, context=None)

    assert "必须接收 context 参数" in str(exc_info.value)


# =============================================================================
# 抽象方法验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_abstract_method_not_implemented():
    """测试未实现抽象方法的子类无法实例化"""
    with pytest.raises(TypeError):
        # IncompleteDecisionProvider 未实现 decide
        _ = IncompleteDecisionProvider({})


# =============================================================================
# start() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_start_basic(mock_context_with_event_bus):
    """测试 start() 基本流程"""
    context, mock_event_bus = mock_context_with_event_bus
    provider = MockDecisionProvider({}, context)

    await provider.start()

    assert provider.event_bus == mock_event_bus
    assert provider.is_started is True
    assert provider.start_called is True


@pytest.mark.asyncio
async def test_decision_provider_start_without_event_bus(mock_context):
    """测试 start() 在没有 event_bus 时也能正常启动"""
    provider = MockDecisionProvider({}, mock_context)

    await provider.start()

    assert provider.event_bus is None
    assert provider.is_started is True
    assert provider.start_called is True


# =============================================================================
# decide() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_decide_basic(mock_provider, mock_normalized_message):
    """测试 decide() 基本功能"""
    # 先 start
    await mock_provider.start()

    # 调用 decide
    intent = await mock_provider.decide(mock_normalized_message)

    # 验证返回值
    assert intent is not None
    assert intent["action"] == "response"
    assert "回复" in intent["text"]
    assert intent["emotion"] == "neutral"
    assert mock_provider.decide_count == 1


@pytest.mark.asyncio
async def test_decision_provider_decide_multiple_calls(mock_provider, mock_normalized_message):
    """测试 decide() 可以多次调用"""
    await mock_provider.start()

    # 多次调用
    intent1 = await mock_provider.decide(mock_normalized_message)
    intent2 = await mock_provider.decide(mock_normalized_message)
    intent3 = await mock_provider.decide(mock_normalized_message)

    assert mock_provider.decide_count == 3
    assert intent1["metadata"]["decide_count"] == 1
    assert intent2["metadata"]["decide_count"] == 2
    assert intent3["metadata"]["decide_count"] == 3


@pytest.mark.asyncio
async def test_decision_provider_decide_before_start(mock_provider, mock_normalized_message):
    """测试未 start 时调用 decide() 仍然可以工作"""
    # decide() 方法不检查 is_started，所以可以调用
    intent = await mock_provider.decide(mock_normalized_message)

    assert intent is not None
    assert mock_provider.decide_count == 1


@pytest.mark.asyncio
async def test_decision_provider_decide_with_error(error_provider, mock_normalized_message):
    """测试 decide() 抛出错误的情况"""
    await error_provider.start()

    with pytest.raises(ValueError) as exc_info:
        await error_provider.decide(mock_normalized_message)

    assert "模拟决策错误" in str(exc_info.value)


# =============================================================================
# stop() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_stop_basic(mock_context):
    """测试 stop() 基本功能"""
    provider = MockDecisionProvider({}, mock_context)
    await provider.start()

    assert provider.is_started is True

    await provider.stop()

    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_decision_provider_stop_idempotent(mock_context):
    """测试 stop() 可以多次调用（幂等）"""
    provider = MockDecisionProvider({}, mock_context)
    await provider.start()

    await provider.stop()
    await provider.stop()
    await provider.stop()

    # 不应该抛出异常
    assert provider.is_started is False


@pytest.mark.asyncio
async def test_decision_provider_stop_preserves_context(mock_context_with_event_bus):
    """测试 stop() 不清除 context 引用"""
    context, mock_event_bus = mock_context_with_event_bus
    provider = MockDecisionProvider({}, context)

    await provider.start()
    assert provider.event_bus == mock_event_bus

    await provider.stop()

    # context 引用仍然存在，但 is_started 为 False
    assert provider.is_started is False
    assert provider.event_bus == mock_event_bus


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_full_lifecycle(mock_context, mock_normalized_message):
    """测试 DecisionProvider 完整生命周期"""
    provider = MockDecisionProvider({}, mock_context)

    # 1. 初始化
    assert provider.is_started is False
    assert provider.start_called is False
    assert provider.stop_called is False

    # 2. 启动
    await provider.start()
    assert provider.is_started is True
    assert provider.start_called is True

    # 3. 决策
    intent = await provider.decide(mock_normalized_message)
    assert intent is not None
    assert provider.decide_count == 1

    # 4. 停止
    await provider.stop()
    assert provider.is_started is False
    assert provider.stop_called is True


@pytest.mark.asyncio
async def test_decision_provider_restart(mock_context, mock_normalized_message):
    """测试 DecisionProvider 可以重新启动"""
    provider = MockDecisionProvider({}, mock_context)

    # 第一次运行
    await provider.start()
    await provider.decide(mock_normalized_message)
    await provider.stop()

    assert provider.decide_count == 1
    assert provider.is_started is False

    # 第二次运行
    await provider.start()
    await provider.decide(mock_normalized_message)
    await provider.stop()

    assert provider.decide_count == 2


@pytest.mark.asyncio
async def test_decision_provider_start_stop_order(mock_context):
    """测试 start 和 stop 的顺序"""
    provider = MockDecisionProvider({}, mock_context)

    # start -> stop
    await provider.start()
    assert provider.start_called is True
    assert provider.stop_called is False

    await provider.stop()
    assert provider.start_called is True  # 保持 True
    assert provider.stop_called is True

    # 再次 start -> stop
    await provider.start()
    assert provider.start_called is True  # 仍然是 True（没有重置）

    await provider.stop()
    assert provider.stop_called is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_with_empty_config(mock_context, mock_normalized_message):
    """测试使用空配置的 DecisionProvider"""
    provider = MockDecisionProvider({}, mock_context)
    await provider.start()

    intent = await provider.decide(mock_normalized_message)

    assert intent is not None
    assert provider.config == {}


@pytest.mark.asyncio
async def test_decision_provider_context_property_access(mock_context_with_event_bus):
    """测试通过 context 属性访问依赖"""
    context, mock_event_bus = mock_context_with_event_bus
    provider = MockDecisionProvider({}, context)

    # event_bus 应该从 context 获取
    assert provider.event_bus == mock_event_bus
    assert provider.context == context


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
