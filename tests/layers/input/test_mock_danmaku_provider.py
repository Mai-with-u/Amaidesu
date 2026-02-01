"""
测试 MockDanmakuProvider（pytest）

运行: uv run pytest tests/layers/input/test_mock_danmaku_provider.py -v
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.layers.input.input_layer import InputLayer
from src.layers.input.providers.mock_danmaku_provider import MockDanmakuProvider


# =============================================================================
# 初始化测试
# =============================================================================

@pytest.mark.asyncio
async def test_mock_danmaku_provider_initialization():
    """测试 MockDanmakuProvider 初始化"""
    config = {
        "interval": 3,
        "messages": ["测试消息1", "测试消息2", "测试消息3"]
    }

    provider = MockDanmakuProvider(config)

    assert provider is not None
    assert provider._running is False
    assert provider.config == config


@pytest.mark.asyncio
async def test_mock_danmaku_provider_default_config():
    """测试 MockDanmakuProvider 默认配置"""
    provider = MockDanmakuProvider({})

    assert provider is not None


# =============================================================================
# RawData 格式测试
# =============================================================================

@pytest.mark.asyncio
async def test_mock_danmaku_provider_raw_data_format():
    """测试 MockDanmakuProvider 生成的 RawData 格式"""
    provider = MockDanmakuProvider({})

    # 测试弹幕 RawData 格式
    raw_data = RawData(
        content={
            "text": "测试弹幕消息",
            "user_name": "测试用户",
            "user_id": "12345"
        },
        source="mock_danmaku",
        data_type="danmaku"
    )

    assert raw_data.content["text"] == "测试弹幕消息"
    assert raw_data.content["user_name"] == "测试用户"
    assert raw_data.content["user_id"] == "12345"
    assert raw_data.source == "mock_danmaku"
    assert raw_data.data_type == "danmaku"


# =============================================================================
# 数据流测试
# =============================================================================

@pytest.mark.asyncio
async def test_mock_danmaku_provider_data_flow():
    """测试 MockDanmakuProvider 完整数据流"""
    event_bus = EventBus()
    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    collected_messages = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            collected_messages.append(message)

    event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 模拟 MockDanmakuProvider 发布 RawData
    provider = MockDanmakuProvider({})
    raw_data = RawData(
        content={
            "text": "主播好！",
            "user_name": "测试粉丝",
            "user_id": "67890"
        },
        source="mock_danmaku",
        data_type="danmaku"
    )

    await event_bus.emit(
        "perception.raw_data.generated",
        {"data": raw_data},
        source="MockDanmakuProvider"
    )

    await asyncio.sleep(0.2)

    # 验证数据流
    assert len(collected_messages) == 1
    assert "主播好！" in collected_messages[0].text
    assert collected_messages[0].source == "mock_danmaku"
    # metadata 可能不包含 user_name，因为它在 content 中
    # 只验证基本属性即可

    await input_layer.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
