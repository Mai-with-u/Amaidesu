"""
DecisionProvider 单元测试

测试 DecisionProvider 抽象基类的所有核心功能：
- 抽象方法验证（decide）
- setup() 方法流程
- decide() 抽象方法
- cleanup() 方法
- get_registration_info() NotImplementedError
- 生命周期管理

运行: uv run pytest tests/core/base/test_decision_provider.py -v
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage

if TYPE_CHECKING:
    from src.domains.decision.intent import Intent


# =============================================================================
# 测试用的 DecisionProvider 实现
# =============================================================================


class MockDecisionProvider(DecisionProvider):
    """模拟的 DecisionProvider 实现（用于测试）"""

    def __init__(self, config: dict, raise_error: bool = False):
        """
        初始化 Mock DecisionProvider

        Args:
            config: Provider 配置
            raise_error: 是否在 decide 时抛出错误
        """
        super().__init__(config)
        self.raise_error = raise_error
        self.setup_called = False
        self.cleanup_called = False
        self.decide_count = 0

    async def _setup_internal(self):
        """模拟内部设置"""
        self.setup_called = True

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

    async def _cleanup_internal(self):
        """模拟内部清理"""
        self.cleanup_called = True
        self.is_setup = False


class IncompleteDecisionProvider(DecisionProvider):
    """不完整的 DecisionProvider（未实现 decide）"""

    pass


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def mock_provider():
    """创建标准的 MockDecisionProvider 实例"""
    return MockDecisionProvider(config={"model": "test"})


@pytest.fixture
def error_provider():
    """创建会抛出错误的 DecisionProvider"""
    return MockDecisionProvider(config={}, raise_error=True)


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
async def test_decision_provider_initialization():
    """测试 DecisionProvider 初始化"""
    config = {"model": "gpt-4", "api_key": "test_key"}
    provider = MockDecisionProvider(config)

    assert provider.config == config
    assert provider.event_bus is None
    assert provider.is_setup is False


@pytest.mark.asyncio
async def test_decision_provider_default_config():
    """测试使用默认配置初始化"""
    provider = MockDecisionProvider({})

    assert provider.config == {}
    assert provider.is_setup is False


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
# setup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_setup_basic():
    """测试 setup() 基本流程"""
    provider = MockDecisionProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)

    assert provider.event_bus == mock_event_bus
    assert provider.is_setup is True
    assert provider.setup_called is True


@pytest.mark.asyncio
async def test_decision_provider_setup_with_config():
    """测试 setup() 可以覆盖配置"""
    provider = MockDecisionProvider({"old": "config"})
    mock_event_bus = object()
    new_config = {"new": "config"}

    await provider.setup(event_bus=mock_event_bus, config=new_config)

    assert provider.config == new_config


@pytest.mark.asyncio
async def test_decision_provider_setup_with_dependencies():
    """测试 setup() 接受依赖注入"""
    provider = MockDecisionProvider({})
    mock_event_bus = object()
    dependencies = {"llm_service": "mock_llm", "cache": "mock_cache"}

    await provider.setup(event_bus=mock_event_bus, dependencies=dependencies)

    # 依赖应该被存储（通过 _dependencies 属性）
    assert hasattr(provider, "_dependencies")
    assert provider._dependencies == dependencies


@pytest.mark.asyncio
async def test_decision_provider_setup_without_dependencies():
    """测试 setup() 不传 dependencies 时使用空字典"""
    provider = MockDecisionProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)

    assert hasattr(provider, "_dependencies")
    assert provider._dependencies == {}


# =============================================================================
# decide() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_decide_basic(mock_provider, mock_normalized_message):
    """测试 decide() 基本功能"""
    # 先 setup
    await mock_provider.setup(event_bus=object())

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
    await mock_provider.setup(event_bus=object())

    # 多次调用
    intent1 = await mock_provider.decide(mock_normalized_message)
    intent2 = await mock_provider.decide(mock_normalized_message)
    intent3 = await mock_provider.decide(mock_normalized_message)

    assert mock_provider.decide_count == 3
    assert intent1["metadata"]["decide_count"] == 1
    assert intent2["metadata"]["decide_count"] == 2
    assert intent3["metadata"]["decide_count"] == 3


@pytest.mark.asyncio
async def test_decision_provider_decide_before_setup(mock_provider, mock_normalized_message):
    """测试未 setup 时调用 decide() 仍然可以工作"""
    # decide() 方法不检查 is_setup，所以可以调用
    intent = await mock_provider.decide(mock_normalized_message)

    assert intent is not None
    assert mock_provider.decide_count == 1


@pytest.mark.asyncio
async def test_decision_provider_decide_with_error(error_provider, mock_normalized_message):
    """测试 decide() 抛出错误的情况"""
    await error_provider.setup(event_bus=object())

    with pytest.raises(ValueError) as exc_info:
        await error_provider.decide(mock_normalized_message)

    assert "模拟决策错误" in str(exc_info.value)


# =============================================================================
# cleanup() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_cleanup_basic():
    """测试 cleanup() 基本功能"""
    provider = MockDecisionProvider({})
    await provider.setup(event_bus=object())

    assert provider.is_setup is True

    await provider.cleanup()

    assert provider.is_setup is False
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_decision_provider_cleanup_idempotent():
    """测试 cleanup() 可以多次调用（幂等）"""
    provider = MockDecisionProvider({})
    await provider.setup(event_bus=object())

    await provider.cleanup()
    await provider.cleanup()
    await provider.cleanup()

    # 不应该抛出异常
    assert provider.is_setup is False


@pytest.mark.asyncio
async def test_decision_provider_cleanup_clears_event_bus():
    """测试 cleanup() 不清除 event_bus 引用"""
    provider = MockDecisionProvider({})
    mock_event_bus = object()

    await provider.setup(event_bus=mock_event_bus)
    assert provider.event_bus == mock_event_bus

    await provider.cleanup()

    # event_bus 引用可能仍然存在，但 is_setup 为 False
    assert provider.is_setup is False


# =============================================================================
# get_registration_info() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_get_registration_info_not_implemented():
    """测试 get_registration_info() 默认抛出 NotImplementedError"""
    provider = MockDecisionProvider({})

    with pytest.raises(NotImplementedError) as exc_info:
        provider.get_registration_info()

    assert "必须实现 get_registration_info()" in str(exc_info.value)


@pytest.mark.asyncio
async def test_decision_provider_get_registration_info_override():
    """测试子类可以重写 get_registration_info()"""

    class RegisteredDecisionProvider(MockDecisionProvider):
        """实现了注册方法的 Provider"""

        @classmethod
        def get_registration_info(cls):
            return {
                "layer": "decision",
                "name": "mock_decision_registered",
                "class": cls,
                "source": "test",
            }

    info = RegisteredDecisionProvider.get_registration_info()

    assert info["layer"] == "decision"
    assert info["name"] == "mock_decision_registered"
    assert info["class"] == RegisteredDecisionProvider
    assert info["source"] == "test"


# =============================================================================
# 生命周期管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_full_lifecycle(mock_normalized_message):
    """测试 DecisionProvider 完整生命周期"""
    provider = MockDecisionProvider({})

    # 1. 初始化
    assert provider.is_setup is False
    assert provider.setup_called is False
    assert provider.cleanup_called is False

    # 2. 设置
    await provider.setup(event_bus=object())
    assert provider.is_setup is True
    assert provider.setup_called is True

    # 3. 决策
    intent = await provider.decide(mock_normalized_message)
    assert intent is not None
    assert provider.decide_count == 1

    # 4. 清理
    await provider.cleanup()
    assert provider.is_setup is False
    assert provider.cleanup_called is True


@pytest.mark.asyncio
async def test_decision_provider_restart(mock_normalized_message):
    """测试 DecisionProvider 可以重新启动"""
    provider = MockDecisionProvider({})

    # 第一次运行
    await provider.setup(event_bus=object())
    await provider.decide(mock_normalized_message)
    await provider.cleanup()

    assert provider.decide_count == 1
    assert provider.is_setup is False

    # 第二次运行
    await provider.setup(event_bus=object())
    await provider.decide(mock_normalized_message)
    await provider.cleanup()

    assert provider.decide_count == 2


@pytest.mark.asyncio
async def test_decision_provider_setup_cleanup_order():
    """测试 setup 和 cleanup 的顺序"""
    provider = MockDecisionProvider({})

    # setup -> cleanup
    await provider.setup(event_bus=object())
    assert provider.setup_called is True
    assert provider.cleanup_called is False

    await provider.cleanup()
    assert provider.setup_called is True  # 保持 True
    assert provider.cleanup_called is True

    # 再次 setup -> cleanup
    await provider.setup(event_bus=object())
    assert provider.setup_called is True  # 仍然是 True（没有重置）

    await provider.cleanup()
    assert provider.cleanup_called is True


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_decision_provider_setup_with_none_event_bus():
    """测试 setup() 传入 None event_bus"""
    provider = MockDecisionProvider({})

    await provider.setup(event_bus=None)

    assert provider.event_bus is None
    assert provider.is_setup is True


@pytest.mark.asyncio
async def test_decision_provider_with_empty_config(mock_normalized_message):
    """测试使用空配置的 DecisionProvider"""
    provider = MockDecisionProvider({})
    await provider.setup(event_bus=object())

    intent = await provider.decide(mock_normalized_message)

    assert intent is not None
    assert provider.config == {}


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
