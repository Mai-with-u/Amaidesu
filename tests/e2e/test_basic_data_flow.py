"""
E2E Test: Basic Data Flow

测试完整的 3 域数据流：
Input → Normalization → Decision → Expression → Output
"""

import asyncio
import pytest

# 导入所有 Provider 模块以触发注册
import src.domains.decision.providers  # noqa: F401
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401

from src.core.base.raw_data import RawData
from src.core.events.names import CoreEvents
from src.core.events.payloads.input import RawDataPayload


@pytest.mark.asyncio
async def test_complete_data_flow_with_mock_providers(event_bus, sample_raw_data, wait_for_event):
    """
    测试完整的数据流：从 RawData 输入到 Intent 生成

    验证：
    1. InputLayer 接收 RawData
    2. 转换为 NormalizedMessage
    3. DecisionProvider 处理并生成 Intent
    """
    from src.domains.input.input_layer import InputLayer
    from src.domains.decision.decision_manager import DecisionManager

    # 1. 设置 InputLayer
    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    # 2. 设置 DecisionManager（使用 mock provider）
    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup("mock", {"default_response": "测试回复"})

    # 3. 等待 normalization.message_ready 事件
    norm_future = asyncio.create_task(wait_for_event(event_bus, CoreEvents.NORMALIZATION_MESSAGE_READY))

    # 4. 等待 decision.intent_generated 事件
    intent_future = asyncio.create_task(wait_for_event(event_bus, CoreEvents.DECISION_INTENT_GENERATED))

    # 5. 发送 RawData
    await event_bus.emit(
        CoreEvents.PERCEPTION_RAW_DATA_GENERATED, {"data": sample_raw_data, "source": "test"}, source="test"
    )

    # 6. 验证 normalization.message_ready 事件
    event_name, event_data, source = await norm_future
    assert event_name == CoreEvents.NORMALIZATION_MESSAGE_READY
    assert "message" in event_data
    normalized_message = event_data["message"]
    assert normalized_message.text == "你好，VTuber"
    assert normalized_message.source == "test_user"

    # 7. 验证 decision.intent_generated 事件
    event_name, event_data, source = await intent_future
    assert event_name == CoreEvents.DECISION_INTENT_GENERATED
    assert "intent" in event_data
    intent = event_data["intent"]
    assert intent.response_text == "[模拟回复] 你好，VTuber"
    assert intent.emotion.value == "neutral"

    # 8. 清理
    await decision_manager.cleanup()
    await input_layer.cleanup()


@pytest.mark.asyncio
async def test_input_layer_normalization(event_bus, sample_raw_data, wait_for_event):
    """
    测试 InputLayer 的标准化功能

    验证：
    1. RawData 正确转换为 NormalizedMessage
    2. 文本内容正确提取
    3. 元数据正确传递
    """
    from src.domains.input.input_layer import InputLayer

    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    # 等待事件
    future = asyncio.create_task(wait_for_event(event_bus, CoreEvents.NORMALIZATION_MESSAGE_READY))

    # 发送 RawData
    await event_bus.emit(
        CoreEvents.PERCEPTION_RAW_DATA_GENERATED, {"data": sample_raw_data, "source": "test"}, source="test"
    )

    # 验证结果
    event_name, event_data, source = await future
    normalized = event_data["message"]

    assert normalized.text == "你好，VTuber"
    assert normalized.source == "test_user"
    assert normalized.data_type == "text"
    assert normalized.importance > 0
    assert "user_id" in normalized.metadata

    await input_layer.cleanup()


@pytest.mark.asyncio
async def test_decision_provider_creates_intent(event_bus, wait_for_event):
    """
    测试 DecisionProvider 创建 Intent

    验证：
    1. DecisionProvider 正确处理 NormalizedMessage
    2. 生成的 Intent 包含正确的字段
    3. 元数据正确传递
    """
    from src.core.base.normalized_message import NormalizedMessage
    from src.domains.decision.decision_manager import DecisionManager
    from src.domains.input.normalization.content import TextContent

    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup("mock", {"default_response": "默认回复"})

    # 创建 NormalizedMessage
    normalized = NormalizedMessage(
        text="测试消息",
        content=TextContent(text="测试消息"),
        source="test_user",
        data_type="text",
        importance=0.8,
        metadata={"test_key": "test_value"},
    )

    # 等待决策事件
    future = asyncio.create_task(wait_for_event(event_bus, CoreEvents.DECISION_INTENT_GENERATED))

    # 发送 NormalizedMessage
    await event_bus.emit(
        CoreEvents.NORMALIZATION_MESSAGE_READY, {"message": normalized, "source": "test"}, source="test"
    )

    # 验证结果
    event_name, event_data, source = await future
    intent = event_data["intent"]

    assert intent.response_text == "[模拟回复] 测试消息"
    assert intent.emotion.value == "neutral"
    assert intent.metadata["mock"] is True
    assert "call_count" in intent.metadata

    await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_multiple_sequential_messages(event_bus, wait_for_event):
    """
    测试多条连续消息的处理

    验证：
    1. 系统能连续处理多条消息
    2. 每条消息独立处理
    3. 处理顺序正确
    """
    from src.domains.input.input_layer import InputLayer
    from src.domains.decision.decision_manager import DecisionManager

    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    decision_manager = DecisionManager(event_bus, llm_service=None)
    await decision_manager.setup("mock", {})

    messages = [RawData(content=f"消息{i}", data_type="text", source="test") for i in range(3)]

    received_intents = []

    async def collect_intents(event_name, event_data, source):
        received_intents.append(event_data["intent"].response_text)

    event_bus.on("decision.intent_generated", collect_intents)

    # 连续发送 3 条消息
    for msg in messages:
        await event_bus.emit("perception.raw_data.generated", RawDataPayload.from_raw_data(msg), source="test")
        await asyncio.sleep(0.1)  # 等待处理

    # 等待所有消息处理完成
    await asyncio.sleep(0.5)

    # 验证
    assert len(received_intents) == 3
    assert all("[模拟回复]" in text for text in received_intents)

    await decision_manager.cleanup()
    await input_layer.cleanup()
