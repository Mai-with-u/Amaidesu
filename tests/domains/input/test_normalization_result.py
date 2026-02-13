"""
测试 NormalizationResult 功能

运行: uv run pytest tests/domains/input/test_normalization_result.py -v
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from pydantic import BaseModel
from typing import Optional

from src.domains.input.coordinator import InputCoordinator
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RawDataPayload
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.raw_data import RawData


# 内部定义的 NormalizationResult 类（与 coordinator.py 保持一致）
class NormalizationResult(BaseModel):
    """标准化结果"""

    success: bool
    message: Optional[NormalizedMessage]
    error: Optional[str] = None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def event_bus():
    """创建事件总线"""
    bus = EventBus()
    yield bus


@pytest.fixture
async def input_coordinator(event_bus):
    """创建 InputCoordinator"""
    coordinator = InputCoordinator(event_bus)
    await coordinator.setup()
    yield coordinator
    await coordinator.cleanup()


# =============================================================================
# NormalizationResult 数据类测试
# =============================================================================


def test_normalization_result_success():
    """测试成功的 NormalizationResult"""
    from src.modules.types.base.normalized_message import NormalizedMessage

    message = NormalizedMessage(
        text="测试消息",
        source="test",
        data_type="text",
        importance=0.5,
    )

    result = NormalizationResult(success=True, message=message, error=None)

    assert result.success is True
    assert result.message is not None
    assert result.message.text == "测试消息"
    assert result.error is None


def test_normalization_result_failure():
    """测试失败的 NormalizationResult"""
    result = NormalizationResult(success=False, message=None, error="转换失败：缺少必要字段")

    assert result.success is False
    assert result.message is None
    assert result.error == "转换失败：缺少必要字段"


# =============================================================================
# InputDomain 失败统计测试
# =============================================================================


@pytest.mark.asyncio
async def test_normalization_error_tracking(input_coordinator):
    """测试标准化处理"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message_dict = event_data.get("message")
        if message_dict:
            results.append(message_dict)

    input_coordinator.event_bus.on(CoreEvents.DATA_MESSAGE, on_message_ready, priority=50)

    # 发送正常消息
    raw_data = RawData(content={"text": "正常消息"}, source="test", data_type="text")

    await input_coordinator.event_bus.emit(CoreEvents.DATA_RAW, RawDataPayload.from_raw_data(raw_data), source="test")

    await asyncio.sleep(0.1)

    # 验证消息成功处理
    assert len(results) == 1
    assert results[0]["text"] == "正常消息"
    assert results[0]["source"] == "test"


@pytest.mark.asyncio
async def test_normalize_returns_result_object():
    """测试 normalize() 方法返回 NormalizationResult 对象"""
    from src.modules.events.event_bus import EventBus
    from src.domains.input.coordinator import InputCoordinator

    event_bus = EventBus()
    coordinator = InputCoordinator(event_bus)

    raw_data = RawData(content={"text": "测试消息"}, source="test", data_type="text")

    result = await coordinator.normalize(raw_data)

    # 验证返回类型 - 注意：这里使用的是 coordinator 内部定义的 NormalizationResult
    # 我们需要验证其结构而不是具体类型
    assert hasattr(result, "success")
    assert hasattr(result, "message")
    assert hasattr(result, "error")
    assert result.success is True
    assert result.message is not None
    assert result.message.text == "测试消息"
    assert result.error is None


@pytest.mark.asyncio
async def test_normalize_with_invalid_data(input_coordinator):
    """测试 normalize() 处理无效数据"""
    # 创建一个可能导致错误的数据
    # 注意：实际测试中需要模拟 Normalizer 抛出异常的情况
    # 这里我们测试数据类型降级处理

    raw_data = RawData(content="未知类型的内容", source="test", data_type="unknown_type")

    result = await input_coordinator.normalize(raw_data)

    # 降级处理应该成功
    assert hasattr(result, "success")
    assert hasattr(result, "message")
    assert hasattr(result, "error")
    assert result.success is True
    assert result.message is not None
    assert "[unknown_type]" in result.message.text


def test_get_stats_not_implemented():
    """测试 get_stats() 方法不存在（已被重构移除）"""
    from src.domains.input.coordinator import InputCoordinator
    from src.modules.events.event_bus import EventBus

    event_bus = EventBus()
    coordinator = InputCoordinator(event_bus)

    # 验证 get_stats 方法不存在
    assert not hasattr(coordinator, "get_stats")


@pytest.mark.asyncio
async def test_multiple_messages_processing():
    """测试多条消息的处理"""
    from src.modules.events.event_bus import EventBus
    from src.domains.input.coordinator import InputCoordinator

    event_bus = EventBus()
    coordinator = InputCoordinator(event_bus)
    await coordinator.setup()

    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        # 这里需要处理 MessageReadyPayload，它是一个序列化的字典
        message_dict = event_data.get("message")
        if message_dict:
            results.append(message_dict)

    event_bus.on(CoreEvents.DATA_MESSAGE, on_message_ready, priority=50)

    # 发送多条消息
    for i in range(5):
        raw_data = RawData(content={"text": f"消息{i}"}, source="test", data_type="text")
        await event_bus.emit(CoreEvents.DATA_RAW, RawDataPayload.from_raw_data(raw_data), source="test")
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.2)

    # 验证所有消息都被正确处理
    assert len(results) == 5
    for i, result in enumerate(results):
        assert result["text"] == f"消息{i}"
        assert result["source"] == "test"

    await coordinator.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
