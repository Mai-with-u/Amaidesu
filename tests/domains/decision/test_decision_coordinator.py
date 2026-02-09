"""
测试 DecisionCoordinator（pytest）

运行: uv run pytest tests/domains/decision/test_decision_coordinator.py -v
"""

import asyncio
import sys
import os
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.core.event_bus import EventBus
from src.core.events.names import CoreEvents
from src.core.events.payloads import MessageReadyPayload
from src.domains.decision.coordinator import DecisionCoordinator
from src.domains.decision.intent import EmotionType, Intent
from src.domains.decision.provider_manager import DecisionProviderManager
from src.domains.input.normalization.content.base import StructuredContent


# =============================================================================
# Mock DecisionProvider（用于测试）
# =============================================================================


class MockDecisionProviderForCoordinator(DecisionProvider):
    """Mock DecisionProvider for DecisionCoordinator testing"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.setup_called = False
        self.cleanup_called = False
        self.decide_responses = []
        self.decide_exception = None
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

        # 检查是否应该抛出异常
        if self.decide_exception:
            raise self.decide_exception

        if not self.decide_responses:
            # 默认响应
            return Intent(
                original_text=message.text,
                response_text="这是默认回复",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"mock": True},
            )

        # 返回预设的响应
        response = self.decide_responses.pop(0)
        return Intent(
            original_text=message.text,
            response_text=response["text"],
            emotion=response["emotion"],
            actions=[],
            metadata={"mock": True},
        )

    def add_response(self, text: str, emotion: EmotionType = EmotionType.NEUTRAL):
        """Add predefined response"""
        self.decide_responses.append(
            {
                "text": text,
                "emotion": emotion,
            }
        )

    def set_exception(self, exception: Exception):
        """Set exception to throw on decide"""
        self.decide_exception = exception


# =============================================================================
# Mock StructuredContent（用于测试）
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
    """创建 EventBus 实例"""
    bus = EventBus(enable_stats=True)
    yield bus
    await bus.cleanup()


@pytest.fixture
async def decision_manager_with_mock(event_bus):
    """创建带有 Mock Provider 的 DecisionProviderManager"""
    manager = DecisionProviderManager(event_bus)

    # 直接创建并设置 mock provider
    mock_provider = MockDecisionProviderForCoordinator()
    await mock_provider.setup(event_bus, {}, None)
    manager._current_provider = mock_provider

    yield manager
    await manager.cleanup()


@pytest.fixture
async def sample_normalized_message():
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


# =============================================================================
# 测试套件: DecisionCoordinator 事件处理
# =============================================================================


@pytest.mark.asyncio
async def test_coordinator_setup_and_cleanup(event_bus, decision_manager_with_mock):
    """测试 DecisionCoordinator 的初始化和清理"""
    coordinator = DecisionCoordinator(event_bus, decision_manager_with_mock)

    await coordinator.setup()
    assert coordinator._event_subscribed is True

    await coordinator.cleanup()
    assert coordinator._event_subscribed is False


@pytest.mark.asyncio
async def test_on_normalized_message_ready_success(event_bus, decision_manager_with_mock, sample_normalized_message):
    """测试处理 normalization.message_ready 事件"""
    coordinator = DecisionCoordinator(event_bus, decision_manager_with_mock)
    decision_manager_with_mock._current_provider.add_response("事件回复", EmotionType.LOVE)

    intent_results = []

    async def intent_handler(event_name: str, event_data: dict, source: str):
        intent_results.append(event_data)

    # 订阅 intent_generated 事件
    event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, intent_handler, priority=50)

    await coordinator.setup()

    # 触发 normalization.message_ready 事件
    await event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        MessageReadyPayload.from_normalized_message(sample_normalized_message),
        source="test",
    )

    await asyncio.sleep(0.2)

    # 验证结果
    assert len(intent_results) == 1
    assert intent_results[0]["intent_data"]["response_text"] == "事件回复"
    assert intent_results[0]["intent_data"]["emotion"] == "love"
    assert intent_results[0]["provider"] == "unknown"  # mock provider 没有设置名称
    assert decision_manager_with_mock._current_provider.call_count == 1

    await coordinator.cleanup()


@pytest.mark.asyncio
async def test_on_normalized_message_ready_injects_source_context(
    event_bus, decision_manager_with_mock, sample_normalized_message
):
    """测试 SourceContext 被正确注入到 Intent"""
    coordinator = DecisionCoordinator(event_bus, decision_manager_with_mock)
    decision_manager_with_mock._current_provider.add_response("回复", EmotionType.NEUTRAL)

    intent_results = []

    async def intent_handler(event_name: str, event_data: dict, source: str):
        intent_results.append(event_data)

    event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, intent_handler, priority=50)

    await coordinator.setup()

    await event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        MessageReadyPayload.from_normalized_message(sample_normalized_message),
        source="test",
    )

    await asyncio.sleep(0.2)

    # 验证 source_context 被注入
    assert len(intent_results) == 1
    assert "source_context" in intent_results[0]["intent_data"]
    source_context = intent_results[0]["intent_data"]["source_context"]
    assert source_context["source"] == "test_source"  # NormalizedMessage.source
    # user_id 在 NormalizedMessage 中不是直接属性，所以是 None
    # metadata 中的 user_id 应该在 extra 中
    assert source_context["user_id"] is None
    assert source_context["extra"]["user_id"] == "test_user_123"  # 从 extra 获取

    await coordinator.cleanup()


@pytest.mark.asyncio
async def test_coordinator_handles_decide_errors(event_bus, decision_manager_with_mock, sample_normalized_message):
    """测试 Coordinator 处理 decide() 抛出异常的情况"""
    coordinator = DecisionCoordinator(event_bus, decision_manager_with_mock)

    # 让 mock provider 抛出异常
    decision_manager_with_mock._current_provider.set_exception(Exception("决策失败"))

    intent_results = []

    async def intent_handler(event_name: str, event_data: dict, source: str):
        intent_results.append(event_data)

    event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, intent_handler, priority=50)

    await coordinator.setup()

    # 触发事件（应该不会崩溃）
    await event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY,
        MessageReadyPayload.from_normalized_message(sample_normalized_message),
        source="test",
    )

    await asyncio.sleep(0.2)

    # 验证没有生成 Intent（因为异常）
    assert len(intent_results) == 0

    await coordinator.cleanup()
