"""
测试 InputLayer 数据流（pytest）

运行: uv run pytest tests/layers/input/test_input_layer.py -v
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.layers.input.input_layer import InputLayer


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def event_bus():
    """创建事件总线"""
    bus = EventBus()
    yield bus


@pytest.fixture
async def input_layer(event_bus):
    """创建 InputLayer"""
    layer = InputLayer(event_bus)
    await layer.setup()
    yield layer
    await layer.cleanup()


# =============================================================================
# InputLayer 核心功能测试
# =============================================================================

@pytest.mark.asyncio
async def test_input_layer_setup(input_layer):
    """测试 InputLayer 初始化"""
    assert input_layer is not None
    assert input_layer.event_bus is not None


@pytest.mark.asyncio
async def test_raw_data_to_normalized_message(input_layer):
    """测试 RawData 转换为 NormalizedMessage"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            results.append(message)

    input_layer.event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 发布测试数据
    raw_data = RawData(
        content={"text": "测试消息"},
        source="test_source",
        data_type="text"
    )

    await input_layer.event_bus.emit(
        "perception.raw_data.generated",
        {"data": raw_data},
        source="test"
    )

    # 等待事件处理
    await asyncio.sleep(0.2)

    # 验证结果
    assert len(results) == 1
    assert results[0].source == "test_source"
    assert results[0].text == "测试消息"


@pytest.mark.asyncio
async def test_input_layer_multiple_messages(input_layer):
    """测试多条消息的连续处理"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            results.append(message)

    input_layer.event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 发送多条消息
    test_messages = ["消息1", "消息2", "消息3"]

    for msg in test_messages:
        raw_data = RawData(
            content={"text": msg},
            source="test",
            data_type="text"
        )
        await input_layer.event_bus.emit(
            "perception.raw_data.generated",
            {"data": raw_data},
            source="test"
        )
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.3)

    # 验证所有消息都被处理
    assert len(results) == 3
    assert [r.text for r in results] == test_messages


@pytest.mark.asyncio
async def test_input_layer_empty_content(input_layer):
    """测试空内容处理"""
    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            results.append(message)

    input_layer.event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 发送空内容
    raw_data = RawData(
        content={},
        source="test",
        data_type="text"
    )

    await input_layer.event_bus.emit(
        "perception.raw_data.generated",
        {"data": raw_data},
        source="test"
    )

    await asyncio.sleep(0.2)

    # 空内容会被转换为 "{}" 文本
    assert len(results) == 1
    assert results[0].text == "{}"


@pytest.mark.asyncio
async def test_full_data_flow():
    """测试完整数据流：RawData -> NormalizedMessage"""
    event_bus = EventBus()
    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    results = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            results.append(message)

    event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 模拟弹幕数据
    raw_data = RawData(
        content={
            "text": "主播好！",
            "user_name": "测试粉丝",
            "user_id": "12345"
        },
        source="bili_danmaku",
        data_type="danmaku"
    )

    await event_bus.emit(
        "perception.raw_data.generated",
        {"data": raw_data},
        source="test"
    )

    await asyncio.sleep(0.2)

    # 验证
    assert len(results) == 1
    assert results[0].source == "bili_danmaku"
    assert "主播好！" in results[0].text or results[0].text == "主播好！"

    await input_layer.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
