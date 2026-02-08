"""
测试 NormalizationResult 功能

运行: uv run pytest tests/domains/input/test_normalization_result.py -v
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.domains.input.input_domain import InputDomain, NormalizationResult
from src.core.events.payloads import RawDataPayload
from src.core.events.names import CoreEvents


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def event_bus():
    """创建事件总线"""
    bus = EventBus()
    yield bus


@pytest.fixture
async def input_domain(event_bus):
    """创建 InputDomain"""
    domain = InputDomain(event_bus)
    await domain.setup()
    yield domain
    await domain.cleanup()


# =============================================================================
# NormalizationResult 数据类测试
# =============================================================================


def test_normalization_result_success():
    """测试成功的 NormalizationResult"""
    from src.core.base.normalized_message import NormalizedMessage
    from src.domains.input.normalization.content import TextContent

    message = NormalizedMessage(
        text="测试消息",
        content=TextContent(text="测试消息"),
        source="test",
        data_type="text",
        importance=0.5,
        metadata={},
        timestamp=0.0,
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
async def test_normalization_error_tracking(input_domain):
    """测试标准化错误统计"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message_dict = event_data.get("message")
        if message_dict:
            results.append(message_dict)

    input_domain.event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 发送正常消息
    raw_data = RawData(content={"text": "正常消息"}, source="test", data_type="text")

    await input_domain.event_bus.emit(
        CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataPayload.from_raw_data(raw_data), source="test"
    )

    await asyncio.sleep(0.1)

    # 获取统计信息
    stats = await input_domain.get_stats()

    assert stats["raw_data_count"] == 1
    assert stats["normalized_message_count"] == 1
    assert stats["normalization_error_count"] == 0
    assert stats["total_processed"] == 1
    assert stats["success_rate"] == 1.0
    assert stats["failure_rate"] == 0.0


@pytest.mark.asyncio
async def test_normalize_returns_result_object(input_domain):
    """测试 normalize() 方法返回 NormalizationResult 对象"""
    raw_data = RawData(content={"text": "测试消息"}, source="test", data_type="text")

    result = await input_domain.normalize(raw_data)

    # 验证返回类型是 NormalizationResult
    assert isinstance(result, NormalizationResult)
    assert result.success is True
    assert result.message is not None
    assert result.message.text == "测试消息"
    assert result.error is None


@pytest.mark.asyncio
async def test_normalize_with_invalid_data(input_domain):
    """测试 normalize() 处理无效数据"""
    # 创建一个可能导致错误的数据
    # 注意：实际测试中需要模拟 Normalizer 抛出异常的情况
    # 这里我们测试数据类型降级处理

    raw_data = RawData(content="未知类型的内容", source="test", data_type="unknown_type")

    result = await input_domain.normalize(raw_data)

    # 降级处理应该成功
    assert isinstance(result, NormalizationResult)
    assert result.success is True
    assert result.message is not None
    assert "[unknown_type]" in result.message.text


@pytest.mark.asyncio
async def test_get_stats_includes_error_tracking(input_domain):
    """测试 get_stats() 包含错误追踪信息"""
    # 初始状态
    stats = await input_domain.get_stats()

    assert "raw_data_count" in stats
    assert "normalized_message_count" in stats
    assert "normalization_error_count" in stats
    assert "total_processed" in stats
    assert "success_rate" in stats
    assert "failure_rate" in stats

    # 初始值应该都是 0
    assert stats["raw_data_count"] == 0
    assert stats["normalized_message_count"] == 0
    assert stats["normalization_error_count"] == 0
    assert stats["total_processed"] == 0
    assert stats["success_rate"] == 0.0
    assert stats["failure_rate"] == 0.0


@pytest.mark.asyncio
async def test_multiple_messages_with_stats(input_domain):
    """测试多条消息的统计"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message_dict = event_data.get("message")
        if message_dict:
            results.append(message_dict)

    input_domain.event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 发送多条消息
    for i in range(5):
        raw_data = RawData(content={"text": f"消息{i}"}, source="test", data_type="text")
        await input_domain.event_bus.emit(
            CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataPayload.from_raw_data(raw_data), source="test"
        )
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.2)

    # 获取统计信息
    stats = await input_domain.get_stats()

    assert stats["raw_data_count"] == 5
    assert stats["normalized_message_count"] == 5
    assert stats["normalization_error_count"] == 0
    assert stats["total_processed"] == 5
    assert stats["success_rate"] == 1.0
    assert stats["failure_rate"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
