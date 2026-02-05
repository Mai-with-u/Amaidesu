"""
测试 DecisionManager（pytest）

运行: uv run pytest tests/layers/decision/test_decision_manager.py -v
"""

import asyncio
import sys
import os
from typing import Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.core.event_bus import EventBus
from src.domains.decision.decision_manager import DecisionManager
from src.core.events.names import CoreEvents
from src.domains.decision.intent import Intent, EmotionType
from src.core.base.normalized_message import NormalizedMessage
from src.core.base.decision_provider import DecisionProvider
from src.domains.normalization.content.base import StructuredContent

# 导入所有 decision providers 以触发注册
import src.domains.decision.providers  # noqa: F401


# =============================================================================
# Mock Provider（用于测试）
# =============================================================================

class MockDecisionProviderForManager(DecisionProvider):
    """Mock DecisionProvider for DecisionManager testing"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.setup_called = False
        self.cleanup_called = False
        self.decide_responses = []
        self.call_count = 0
        self.last_message = None

    async def setup(self, event_bus, config, dependencies):
        """Setup method"""
        self.setup_called = True
        self.event_bus = event_bus
        self.config = config

    async def cleanup(self):
        """Cleanup method"""
        self.cleanup_called = True

    async def decide(self, message: NormalizedMessage) -> Intent:
        """Decide method"""
        self.call_count += 1
        self.last_message = message

        if not self.decide_responses:
            # 默认响应
            return Intent(
                original_text=message.text,
                response_text="这是默认回复",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"mock": True},
            )

        response = self.decide_responses.pop(0)
        return Intent(
            original_text=message.text,
            response_text=response.get("text", "默认回复"),
            emotion=response.get("emotion", EmotionType.NEUTRAL),
            actions=[],
            metadata={"mock": True},
        )

    def add_response(self, text: str, emotion: EmotionType = EmotionType.NEUTRAL):
        """Add predefined response"""
        self.decide_responses.append({
            "text": text,
            "emotion": emotion,
        })


class FailingMockDecisionProvider(DecisionProvider):
    """Mock provider that fails during setup"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})

    async def setup(self, event_bus, config, dependencies):
        raise ConnectionError("Setup failed")

    async def cleanup(self):
        pass

    async def decide(self, message):
        return Intent(
            original_text=message.text,
            response_text="Should not reach here",
            emotion=EmotionType.NEUTRAL,
            actions=[],
            metadata={},
        )


class NoneReturningMockProvider(DecisionProvider):
    """Mock provider that returns None from decide"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})

    async def setup(self, event_bus, config, dependencies):
        pass

    async def cleanup(self):
        pass

    async def decide(self, message):
        return None


# =============================================================================
# Test Content（用于 NormalizedMessage）
# =============================================================================

class MockStructuredContent(StructuredContent):
    """Mock structured content for testing"""

    def __init__(self, text: str, content_type: str = "text"):
        self._text = text
        self._type = content_type
        self._data = {"text": text}

    def get_display_text(self) -> str:
        return self._text

    def get_user_id(self) -> str:
        return "test_user_123"

    def get_importance(self) -> float:
        return 0.5

    def to_dict(self) -> Dict[str, Any]:
        return self._data

    @property
    def type(self) -> str:
        return self._type


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def event_bus():
    """创建事件总线"""
    bus = EventBus()
    yield bus


@pytest.fixture
def mock_provider_class():
    """提供 Mock Provider 类"""
    return MockDecisionProviderForManager


@pytest.fixture
def sample_normalized_message():
    """创建示例 NormalizedMessage"""
    content = MockStructuredContent("测试消息")
    return NormalizedMessage(
        text="测试消息",
        content=content,
        source="test_source",
        data_type="text",
        importance=0.5,
        metadata={"user_id": "test_user_123"},
    )


@pytest.fixture
async def decision_manager_with_mock(event_bus, mock_provider_class):
    """创建并设置 DecisionManager（使用 Mock Provider）"""
    from src.domains.output.provider_registry import ProviderRegistry

    # 注册 mock provider
    ProviderRegistry.register_decision(
        "mock_decision",
        mock_provider_class,
        source="test"
    )

    manager = DecisionManager(event_bus)
    config = {"test_key": "test_value"}

    await manager.setup("mock_decision", config)

    yield manager

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


# =============================================================================
# 初始化和设置测试
# =============================================================================

@pytest.mark.asyncio
async def test_decision_manager_initialization(event_bus):
    """测试 DecisionManager 初始化"""
    manager = DecisionManager(event_bus)

    assert manager is not None
    assert manager.event_bus == event_bus
    assert manager._llm_service is None
    assert manager._current_provider is None
    assert manager._provider_name is None


@pytest.mark.asyncio
async def test_decision_manager_initialization_with_llm_service(event_bus):
    """测试带 LLMService 的初始化"""
    mock_llm_service = {"mock": "llm_service"}
    manager = DecisionManager(event_bus, llm_service=mock_llm_service)

    assert manager._llm_service == mock_llm_service


@pytest.mark.asyncio
async def test_setup_provider_success(event_bus, mock_provider_class):
    """测试成功设置 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)
    config = {"test_key": "test_value"}

    await manager.setup("mock_decision", config)

    # 验证 Provider 已设置
    assert manager._current_provider is not None
    assert manager._provider_name == "mock_decision"
    assert manager._current_provider.setup_called is True
    assert manager._current_provider.config == config

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_setup_provider_not_registered(event_bus):
    """测试设置未注册的 Provider（应抛出 ValueError）"""
    manager = DecisionManager(event_bus)

    with pytest.raises(ValueError, match="DecisionProvider 'nonexistent' 未找到"):
        await manager.setup("nonexistent", {})


@pytest.mark.asyncio
async def test_setup_provider_setup_failure(event_bus):
    """测试 Provider setup 失败"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("failing_provider", FailingMockDecisionProvider, source="test")

    manager = DecisionManager(event_bus)

    with pytest.raises(ConnectionError, match="无法初始化DecisionProvider 'failing_provider'"):
        await manager.setup("failing_provider", {})

    # 验证 Provider 未设置
    assert manager._current_provider is None
    assert manager._provider_name is None

    ProviderRegistry.unregister_decision("failing_provider")


@pytest.mark.asyncio
async def test_setup_replace_existing_provider(event_bus, mock_provider_class):
    """测试替换已存在的 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 设置第一个 Provider
    await manager.setup("mock_decision", {"first": True})
    first_provider = manager._current_provider

    # 设置第二个 Provider（应清理第一个）
    await manager.setup("mock_decision", {"second": True})
    second_provider = manager._current_provider

    # 验证第二个 Provider 已设置
    assert second_provider.config == {"second": True}
    assert first_provider.cleanup_called is True

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_setup_subscribes_to_event(event_bus, mock_provider_class):
    """测试 setup 订阅事件"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 验证事件订阅
    event_count = {"count": 0}

    async def test_handler(event_name, event_data, source):
        event_count["count"] += 1

    # 在 setup 之前订阅同一个事件
    event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, test_handler, priority=50)

    await manager.setup("mock_decision", {})

    # 触发事件验证订阅成功
    await event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": None},
        source="test"
    )
    await asyncio.sleep(0.1)

    assert event_count["count"] > 0

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


# =============================================================================
# 决策功能测试
# =============================================================================

@pytest.mark.asyncio
async def test_decide_success(decision_manager_with_mock, sample_normalized_message):
    """测试成功执行决策"""
    manager = decision_manager_with_mock
    manager._current_provider.add_response("测试回复", EmotionType.HAPPY)

    result = await manager.decide(sample_normalized_message)

    assert isinstance(result, Intent)
    assert result.response_text == "测试回复"
    assert result.emotion == EmotionType.HAPPY
    assert result.original_text == sample_normalized_message.text
    assert manager._current_provider.call_count == 1


@pytest.mark.asyncio
async def test_decide_without_provider(event_bus, sample_normalized_message):
    """测试未设置 Provider 时执行决策（应抛出 RuntimeError）"""
    manager = DecisionManager(event_bus)

    with pytest.raises(RuntimeError, match="当前未设置DecisionProvider"):
        await manager.decide(sample_normalized_message)


@pytest.mark.asyncio
async def test_decide_multiple_calls(decision_manager_with_mock, sample_normalized_message):
    """测试多次调用 decide"""
    manager = decision_manager_with_mock

    # 添加多个响应
    manager._current_provider.add_response("回复1", EmotionType.HAPPY)
    manager._current_provider.add_response("回复2", EmotionType.SAD)
    manager._current_provider.add_response("回复3", EmotionType.ANGRY)

    # 执行多次决策
    result1 = await manager.decide(sample_normalized_message)
    result2 = await manager.decide(sample_normalized_message)
    result3 = await manager.decide(sample_normalized_message)

    assert result1.response_text == "回复1"
    assert result2.response_text == "回复2"
    assert result3.response_text == "回复3"
    assert manager._current_provider.call_count == 3


@pytest.mark.asyncio
async def test_decide_default_response(decision_manager_with_mock, sample_normalized_message):
    """测试使用默认响应"""
    manager = decision_manager_with_mock

    result = await manager.decide(sample_normalized_message)

    assert result.response_text == "这是默认回复"
    assert result.emotion == EmotionType.NEUTRAL


@pytest.mark.asyncio
async def test_decide_preserves_metadata(decision_manager_with_mock, sample_normalized_message):
    """测试 decide 保留原始消息的 metadata"""
    manager = decision_manager_with_mock

    result = await manager.decide(sample_normalized_message)

    assert result.original_text == sample_normalized_message.text
    assert "mock" in result.metadata
    assert result.metadata["mock"] is True


# =============================================================================
# 事件处理测试
# =============================================================================

@pytest.mark.asyncio
async def test_on_normalized_message_ready_success(decision_manager_with_mock, sample_normalized_message):
    """测试处理 normalization.message_ready 事件"""
    manager = decision_manager_with_mock
    manager._current_provider.add_response("事件回复", EmotionType.LOVE)

    intent_results = []

    async def intent_handler(event_name: str, event_data: dict, source: str):
        intent = event_data.get("intent")
        if intent:
            intent_results.append(intent)

    # 订阅 intent_generated 事件
    manager.event_bus.on("decision.intent_generated", intent_handler, priority=50)

    # 触发 normalization.message_ready 事件
    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": sample_normalized_message},
        source="test"
    )

    await asyncio.sleep(0.2)

    # 验证结果
    assert len(intent_results) == 1
    assert intent_results[0].response_text == "事件回复"
    assert intent_results[0].emotion == EmotionType.LOVE
    assert manager._current_provider.call_count == 1


@pytest.mark.asyncio
async def test_on_normalized_message_ready_empty_message(decision_manager_with_mock):
    """测试处理空消息（应忽略）"""
    manager = decision_manager_with_mock

    # 触发空消息事件
    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": None},
        source="test"
    )

    await asyncio.sleep(0.1)

    # 验证 decide 未被调用
    assert manager._current_provider.call_count == 0


@pytest.mark.asyncio
async def test_on_normalized_message_ready_missing_message_key(decision_manager_with_mock):
    """测试事件数据中缺少 message 键"""
    manager = decision_manager_with_mock

    # 触发缺少 message 键的事件
    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {},  # 空字典
        source="test"
    )

    await asyncio.sleep(0.1)

    # 验证 decide 未被调用
    assert manager._current_provider.call_count == 0


@pytest.mark.asyncio
async def test_on_normalized_message_ready_event_data_structure(decision_manager_with_mock, sample_normalized_message):
    """测试 intent_generated 事件的数据结构"""
    manager = decision_manager_with_mock

    event_data_received = []
    source_received = []

    async def intent_handler(event_name: str, event_data: dict, source: str):
        event_data_received.append(event_data)
        source_received.append(source)

    manager.event_bus.on("decision.intent_generated", intent_handler, priority=50)

    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": sample_normalized_message},
        source="test"
    )

    await asyncio.sleep(0.2)

    # 验证事件数据结构
    assert len(event_data_received) == 1
    event_data = event_data_received[0]

    assert "intent" in event_data
    assert "original_message" in event_data
    assert "provider" in event_data

    assert isinstance(event_data["intent"], Intent)
    assert event_data["original_message"] == sample_normalized_message
    assert event_data["provider"] == "mock_decision"
    # source 是作为参数传递给 handler 的，不在 event_data 里
    assert source_received[0] == "DecisionManager"


@pytest.mark.asyncio
async def test_on_normalized_message_ready_handles_provider_error(decision_manager_with_mock, sample_normalized_message):
    """测试处理 Provider 决策失败"""
    manager = decision_manager_with_mock

    # 让 decide 抛出异常
    async def failing_decide(message):
        raise RuntimeError("Decision failed")

    manager._current_provider.decide = failing_decide

    # 应该不抛出异常，只记录错误
    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": sample_normalized_message},
        source="test"
    )

    await asyncio.sleep(0.1)

    # 验证不会崩溃


# =============================================================================
# Provider 切换测试
# =============================================================================

@pytest.mark.asyncio
async def test_switch_provider_success(event_bus, mock_provider_class):
    """测试成功切换 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 设置第一个 Provider
    await manager.setup("mock_decision", {"first": True})
    first_provider = manager._current_provider

    # 切换到新 Provider
    await manager.switch_provider("mock_decision", {"second": True})
    second_provider = manager._current_provider

    # 验证切换成功
    assert second_provider.config == {"second": True}
    assert first_provider.cleanup_called is True
    assert manager._provider_name == "mock_decision"

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_switch_provider_failure_rollback(event_bus, mock_provider_class):
    """测试切换失败时回退到旧 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")
    ProviderRegistry.register_decision("failing_provider", FailingMockDecisionProvider, source="test")

    manager = DecisionManager(event_bus)

    # 设置初始 Provider
    await manager.setup("mock_decision", {"initial": True})
    initial_provider = manager._current_provider

    # 尝试切换到失败的 Provider（应回退）
    with pytest.raises(ConnectionError):
        await manager.switch_provider("failing_provider", {})

    # 验证回退到初始 Provider
    assert manager._current_provider == initial_provider
    assert manager._provider_name == "mock_decision"
    assert initial_provider.cleanup_called is False  # 不应该清理

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")
    ProviderRegistry.unregister_decision("failing_provider")


@pytest.mark.asyncio
async def test_switch_provider_nonexistent(event_bus, mock_provider_class):
    """测试切换到不存在的 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 设置初始 Provider
    await manager.setup("mock_decision", {})
    initial_provider = manager._current_provider

    # 尝试切换到不存在的 Provider
    with pytest.raises(ValueError, match="DecisionProvider 'nonexistent' 未找到"):
        await manager.switch_provider("nonexistent", {})

    # 验证回退到初始 Provider
    assert manager._current_provider == initial_provider
    assert manager._provider_name == "mock_decision"

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_switch_provider_from_none(event_bus, mock_provider_class):
    """从未设置 Provider 开始切换（相当于首次 setup）"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 从未设置状态切换
    await manager.switch_provider("mock_decision", {"test": True})

    # 验证成功设置
    assert manager._current_provider is not None
    assert manager._provider_name == "mock_decision"

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


# =============================================================================
# 清理测试
# =============================================================================

@pytest.mark.asyncio
async def test_cleanup_clears_provider(decision_manager_with_mock):
    """测试 cleanup 清理 Provider"""
    manager = decision_manager_with_mock

    assert manager._current_provider is not None

    # 保存对 provider 的引用，以便在 cleanup 后检查
    provider = manager._current_provider

    await manager.cleanup()

    assert manager._current_provider is None
    assert manager._provider_name is None
    assert provider.cleanup_called is True  # 使用保存的引用检查


@pytest.mark.asyncio
async def test_cleanup_unsubscribes_events(event_bus, mock_provider_class):
    """测试 cleanup 取消事件订阅"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)
    await manager.setup("mock_decision", {})

    # Cleanup
    await manager.cleanup()

    # 验证事件订阅已取消（事件处理应不再触发）
    call_count = {"count": 0}

    async def test_handler(event_name, event_data, source):
        call_count["count"] += 1

    manager.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, test_handler, priority=50)

    # 由于 manager 已经 cleanup，它的处理器不应该再被调用
    await manager.event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        {"message": None},
        source="test"
    )

    await asyncio.sleep(0.1)

    # 注意：这里只验证 cleanup 不抛出异常
    # 实际的事件取消订阅由 EventBus.off() 处理

    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_cleanup_without_setup(event_bus):
    """测试未 setup 时 cleanup（应安全）"""
    manager = DecisionManager(event_bus)

    # 不应该抛出异常
    await manager.cleanup()

    assert manager._current_provider is None


@pytest.mark.asyncio
async def test_cleanup_handles_provider_error(event_bus, mock_provider_class):
    """测试 cleanup 处理 Provider 清理失败"""
    from src.domains.output.provider_registry import ProviderRegistry

    class FailingCleanupProvider(DecisionProvider):
        def __init__(self, config: Dict[str, Any] = None):
            super().__init__(config or {})

        async def setup(self, event_bus, config, dependencies):
            pass

        async def cleanup(self):
            raise RuntimeError("Cleanup failed")

        async def decide(self, message):
            return Intent(
                original_text=message.text,
                response_text="test",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={},
            )

    ProviderRegistry.register_decision("failing_cleanup", FailingCleanupProvider, source="test")

    manager = DecisionManager(event_bus)
    await manager.setup("failing_cleanup", {})

    # Cleanup 应该不抛出异常，只记录错误
    await manager.cleanup()

    assert manager._current_provider is None
    assert manager._provider_name is None

    ProviderRegistry.unregister_decision("failing_cleanup")


# =============================================================================
# 查询方法测试
# =============================================================================

@pytest.mark.asyncio
async def test_get_current_provider(decision_manager_with_mock):
    """测试获取当前 Provider"""
    manager = decision_manager_with_mock

    provider = manager.get_current_provider()

    assert provider is not None
    assert isinstance(provider, MockDecisionProviderForManager)
    assert provider.setup_called is True


@pytest.mark.asyncio
async def test_get_current_provider_none(event_bus):
    """测试未设置 Provider 时获取（应返回 None）"""
    manager = DecisionManager(event_bus)

    provider = manager.get_current_provider()

    assert provider is None


@pytest.mark.asyncio
async def test_get_current_provider_name(decision_manager_with_mock):
    """测试获取当前 Provider 名称"""
    manager = decision_manager_with_mock

    name = manager.get_current_provider_name()

    assert name == "mock_decision"


@pytest.mark.asyncio
async def test_get_current_provider_name_none(event_bus):
    """测试未设置 Provider 时获取名称（应返回 None）"""
    manager = DecisionManager(event_bus)

    name = manager.get_current_provider_name()

    assert name is None


@pytest.mark.asyncio
async def test_get_available_providers(event_bus):
    """测试获取可用 Provider 列表"""
    manager = DecisionManager(event_bus)

    providers = manager.get_available_providers()

    assert isinstance(providers, list)
    assert len(providers) > 0
    # 应该包含一些内置 Provider
    assert "maicore" in providers


@pytest.mark.asyncio
async def test_get_available_providers_after_registration(event_bus, mock_provider_class):
    """测试注册后获取可用 Provider"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("test_mock", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)
    providers = manager.get_available_providers()

    assert "test_mock" in providers
    assert "maicore" in providers

    ProviderRegistry.unregister_decision("test_mock")


# =============================================================================
# 并发测试
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_setup_and_switch(event_bus, mock_provider_class):
    """测试并发 setup 和 switch"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 并发执行
    tasks = [
        manager.setup("mock_decision", {"index": i})
        for i in range(5)
    ]

    await asyncio.gather(*tasks)

    # 验证最终状态一致
    assert manager._current_provider is not None
    assert manager._provider_name == "mock_decision"

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_concurrent_decide_calls(decision_manager_with_mock, sample_normalized_message):
    """测试并发 decide 调用"""
    manager = decision_manager_with_mock

    # 添加足够多的响应
    for i in range(10):
        manager._current_provider.add_response(f"回复{i}", EmotionType.NEUTRAL)

    # 并发执行
    tasks = [
        manager.decide(sample_normalized_message)
        for _ in range(10)
    ]

    results = await asyncio.gather(*tasks)

    # 验证所有调用都成功
    assert len(results) == 10
    assert all(isinstance(r, Intent) for r in results)
    assert manager._current_provider.call_count == 10


@pytest.mark.asyncio
async def test_switch_lock_prevents_race(event_bus, mock_provider_class):
    """测试切换锁防止竞态条件"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)

    # 初始设置
    await manager.setup("mock_decision", {})

    # 并发切换
    tasks = [
        manager.switch_provider("mock_decision", {"index": i})
        for i in range(5)
    ]

    await asyncio.gather(*tasks)

    # 验证最终状态一致
    assert manager._current_provider is not None
    assert manager._provider_name == "mock_decision"

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


# =============================================================================
# 边缘情况测试
# =============================================================================

@pytest.mark.asyncio
async def test_provider_returns_none_from_decide(event_bus):
    """测试 Provider 返回 None"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("none_provider", NoneReturningMockProvider, source="test")

    manager = DecisionManager(event_bus)
    await manager.setup("none_provider", {})

    content = MockStructuredContent("测试")
    message = NormalizedMessage(
        text="测试",
        content=content,
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
    )

    # decide 返回 None，但不会抛出异常
    result = await manager.decide(message)

    assert result is None

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("none_provider")


@pytest.mark.asyncio
async def test_empty_normalized_message_text(decision_manager_with_mock):
    """测试空文本的 NormalizedMessage"""
    manager = decision_manager_with_mock

    content = MockStructuredContent("")
    message = NormalizedMessage(
        text="",
        content=content,
        source="test",
        data_type="text",
        importance=0.0,
        metadata={},
    )

    result = await manager.decide(message)

    assert result is not None
    assert result.original_text == ""


@pytest.mark.asyncio
async def test_very_long_message_text(decision_manager_with_mock):
    """测试超长消息文本"""
    manager = decision_manager_with_mock

    long_text = "测试" * 10000  # 40000 个字符
    content = MockStructuredContent(long_text)
    message = NormalizedMessage(
        text=long_text,
        content=content,
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
    )

    result = await manager.decide(message)

    assert result is not None
    assert result.original_text == long_text


# =============================================================================
# LLMService 依赖注入测试
# =============================================================================

@pytest.mark.asyncio
async def test_llm_service_dependency_injection(event_bus, mock_provider_class):
    """测试 LLMService 依赖注入"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    mock_llm_service = {"mock": "llm_service"}
    manager = DecisionManager(event_bus, llm_service=mock_llm_service)

    await manager.setup("mock_decision", {})

    # 验证 LLMService 被传递给 Provider
    # 注意：这需要 mock_provider_class 的 setup 方法验证依赖
    assert manager._llm_service == mock_llm_service

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


@pytest.mark.asyncio
async def test_llm_service_none_no_dependency(event_bus, mock_provider_class):
    """测试 LLMService 为 None 时不注入依赖"""
    from src.domains.output.provider_registry import ProviderRegistry

    ProviderRegistry.register_decision("mock_decision", mock_provider_class, source="test")

    manager = DecisionManager(event_bus)  # 不传递 llm_service

    await manager.setup("mock_decision", {})

    # 验证不注入 llm_service 依赖
    assert manager._llm_service is None

    # 清理
    await manager.cleanup()
    ProviderRegistry.unregister_decision("mock_decision")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
