"""
E2E Test: Smoke Test

快速验证系统基本功能
"""

import pytest
from pydantic import BaseModel, Field

# 在模块级别导入，触发 Provider 注册
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401
import src.domains.decision.providers  # noqa: F401


class SimpleTestEvent(BaseModel):
    """简单测试事件"""
    data: str = Field(..., description="测试数据")


@pytest.mark.asyncio
async def test_provider_registry_has_providers():
    """测试 Provider 注册表中有 Provider"""
    from src.core.provider_registry import ProviderRegistry

    input_providers = ProviderRegistry.get_registered_input_providers()
    output_providers = ProviderRegistry.get_registered_output_providers()
    decision_providers = ProviderRegistry.get_registered_decision_providers()

    assert len(input_providers) > 0, "应该有 InputProvider"
    assert len(output_providers) > 0, "应该有 OutputProvider"
    assert len(decision_providers) > 0, "应该有 DecisionProvider"

    # 验证特定的 Provider 存在
    assert "mock" in decision_providers, "应该有 MockDecisionProvider"
    assert "mock" in output_providers, "应该有 MockOutputProvider"
    assert "mock_danmaku" in input_providers, "应该有 MockDanmakuInputProvider"


@pytest.mark.asyncio
async def test_event_bus_creation():
    """测试 EventBus 可以正常创建"""
    from src.core.event_bus import EventBus

    bus = EventBus()
    assert bus is not None

    # 测试基本事件发布/订阅
    received = []

    async def listener(event_name, data, source):
        received.append((event_name, data, source))

    bus.on("test.event", listener)
    await bus.emit("test.event", SimpleTestEvent(data="test"), source="test")

    assert len(received) == 1
    assert received[0][0] == "test.event"
    assert received[0][1]["data"] == "test"  # 序列化后的dict仍然可以用dict方式访问
    assert received[0][2] == "test"


@pytest.mark.asyncio
async def test_mock_decision_provider():
    """测试 MockDecisionProvider 基本功能"""
    from src.domains.decision.providers.mock import MockDecisionProvider
    from src.core.base.normalized_message import NormalizedMessage
    from src.domains.input.normalization.content import TextContent
    from src.core.event_bus import EventBus

    provider = MockDecisionProvider({})
    await provider.setup(EventBus(), {}, {})

    # 创建测试消息
    message = NormalizedMessage(
        text="测试消息",
        content=TextContent(text="测试消息"),
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
        timestamp=None,
    )

    # 测试决策
    intent = await provider.decide(message)

    assert intent is not None
    assert "[模拟回复]" in intent.response_text
    assert intent.emotion.value == "neutral"
    assert provider.get_call_count() == 1

    await provider.cleanup()
