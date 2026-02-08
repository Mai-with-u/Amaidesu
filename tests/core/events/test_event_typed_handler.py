"""
测试类型化事件处理器

验证 EventBus.on_typed() 方法的功能：
- 自动反序列化 Pydantic Model
- 处理器直接接收类型化对象
- 无需手动 from_dict() 调用
"""

import pytest
import asyncio
from pydantic import BaseModel, Field
from typing import Dict, Any

from src.core.event_bus import EventBus
from src.core.events.payloads.input import MessageReadyPayload
from src.core.events.payloads.decision import IntentPayload
from src.core.events.names import CoreEvents


class TestTypedEventHandler:
    """测试类型化事件处理器"""

    @pytest.mark.asyncio
    async def test_on_typed_basic(self):
        """测试基本的类型化事件订阅"""

        # 定义测试数据模型
        class TestPayload(BaseModel):
            value: str = Field(..., description="测试值")
            count: int = Field(default=0, description="计数")

        event_bus = EventBus(enable_stats=False)

        # 存储接收到的数据
        received_data = []

        # 定义类型化处理器
        async def typed_handler(event_name: str, data: TestPayload, source: str):
            # data 应该是 TestPayload 类型，而不是 dict
            assert isinstance(data, TestPayload)
            assert isinstance(data.value, str)
            assert isinstance(data.count, int)
            received_data.append((event_name, data, source))

        # 订阅类型化事件
        event_bus.on_typed("test.typed_event", typed_handler, TestPayload)

        # 发布事件（传入 TestPayload 对象）
        payload = TestPayload(value="hello", count=42)
        await event_bus.emit("test.typed_event", payload, source="test_source")

        # 等待事件处理完成
        await asyncio.sleep(0.1)

        # 验证处理器被调用，且接收到的是类型化对象
        assert len(received_data) == 1
        event_name, data, source = received_data[0]
        assert event_name == "test.typed_event"
        assert isinstance(data, TestPayload)
        assert data.value == "hello"
        assert data.count == 42
        assert source == "test_source"

    @pytest.mark.asyncio
    async def test_on_typed_with_message_ready_payload(self):
        """测试使用 MessageReadyPayload 的类型化事件"""

        event_bus = EventBus(enable_stats=False)

        received_messages = []

        # 定义类型化处理器
        async def message_handler(event_name: str, payload: MessageReadyPayload, source: str):
            # payload 应该是 MessageReadyPayload 类型
            assert isinstance(payload, MessageReadyPayload)
            assert isinstance(payload.message, dict)
            received_messages.append(payload)

        # 订阅类型化事件
        event_bus.on_typed(
            CoreEvents.NORMALIZATION_MESSAGE_READY,
            message_handler,
            MessageReadyPayload,
        )

        # 发布事件（传入 MessageReadyPayload 对象）
        payload = MessageReadyPayload(
            message={"text": "测试消息", "source": "test"},
            source="test_source",
        )
        await event_bus.emit(CoreEvents.NORMALIZATION_MESSAGE_READY, payload, source="test")

        # 等待事件处理完成
        await asyncio.sleep(0.1)

        # 验证处理器接收到类型化对象
        assert len(received_messages) == 1
        assert isinstance(received_messages[0], MessageReadyPayload)
        assert received_messages[0].source == "test_source"
        assert received_messages[0].message["text"] == "测试消息"

    @pytest.mark.asyncio
    async def test_on_typed_with_intent_payload(self):
        """测试使用 IntentPayload 的类型化事件"""

        event_bus = EventBus(enable_stats=False)

        received_intents = []

        # 定义类型化处理器
        async def intent_handler(event_name: str, payload: IntentPayload, source: str):
            # payload 应该是 IntentPayload 类型
            assert isinstance(payload, IntentPayload)
            # 可以直接调用 to_intent() 方法
            intent = payload.to_intent()
            received_intents.append(intent)

        # 订阅类型化事件
        event_bus.on_typed(
            CoreEvents.DECISION_INTENT_GENERATED,
            intent_handler,
            IntentPayload,
        )

        # 发布事件（传入 IntentPayload 对象）
        payload = IntentPayload(
            intent_data={
                "original_text": "你好",
                "response_text": "你好！",
                "emotion": "happy",
                "actions": [],
            },
            provider="test_provider",
        )
        await event_bus.emit(CoreEvents.DECISION_INTENT_GENERATED, payload, source="test")

        # 等待事件处理完成
        await asyncio.sleep(0.1)

        # 验证处理器接收到类型化对象并成功转换
        assert len(received_intents) == 1
        assert received_intents[0].original_text == "你好"
        assert received_intents[0].response_text == "你好！"

    @pytest.mark.asyncio
    async def test_on_typed_priority(self):
        """测试类型化事件的优先级"""

        event_bus = EventBus(enable_stats=False)

        execution_order = []

        # 定义多个处理器
        async def handler_high(event_name: str, data: BaseModel, source: str):
            execution_order.append("high")

        async def handler_low(event_name: str, data: BaseModel, source: str):
            execution_order.append("low")

        async def handler_default(event_name: str, data: BaseModel, source: str):
            execution_order.append("default")

        class TestPayload(BaseModel):
            value: str

        # 订阅不同优先级
        event_bus.on_typed("test.priority", handler_default, TestPayload, priority=100)
        event_bus.on_typed("test.priority", handler_high, TestPayload, priority=10)
        event_bus.on_typed("test.priority", handler_low, TestPayload, priority=200)

        # 发布事件
        payload = TestPayload(value="test")
        await event_bus.emit("test.priority", payload, source="test")

        # 等待事件处理完成
        await asyncio.sleep(0.1)

        # 验证执行顺序（数字越小越优先）
        assert execution_order == ["high", "default", "low"]

    @pytest.mark.asyncio
    async def test_on_typed_mixed_with_regular_on(self):
        """测试类型化订阅和普通订阅可以混合使用"""

        event_bus = EventBus(enable_stats=False)

        received_typed = []
        received_regular = []

        class TestPayload(BaseModel):
            value: str

        # 类型化处理器
        async def typed_handler(event_name: str, data: TestPayload, source: str):
            assert isinstance(data, TestPayload)
            received_typed.append(data.value)

        # 普通处理器
        async def regular_handler(event_name: str, data: dict, source: str):
            assert isinstance(data, dict)
            received_regular.append(data["value"])

        # 混合订阅
        event_bus.on_typed("test.mixed", typed_handler, TestPayload)
        event_bus.on("test.mixed", regular_handler)

        # 发布事件
        payload = TestPayload(value="test_value")
        await event_bus.emit("test.mixed", payload, source="test")

        # 等待事件处理完成
        await asyncio.sleep(0.1)

        # 验证两种处理器都被调用
        assert len(received_typed) == 1
        assert len(received_regular) == 1
        assert received_typed[0] == "test_value"
        assert received_regular[0] == "test_value"
