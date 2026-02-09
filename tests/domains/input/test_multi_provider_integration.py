"""
测试多 Provider 集成（pytest）

运行: uv run pytest tests/domains/input/test_multi_provider_integration.py -v
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.core.events.payloads.input import RawDataPayload
from src.domains.input.coordinator import InputCoordinator
from src.domains.input.providers import ConsoleInputProvider, MockDanmakuInputProvider as MockDanmakuProvider


@pytest.mark.asyncio
async def test_multiple_providers_concurrent():
    """测试多个 Provider 并发工作"""
    event_bus = EventBus()
    input_coordinator = InputCoordinator(event_bus)
    await input_coordinator.setup()

    collected_messages = []

    async def on_message_ready(event_name: str, event_data: dict, source: str):
        message = event_data.get("message")
        if message:
            collected_messages.append(message)

    event_bus.on("normalization.message_ready", on_message_ready, priority=50)

    # 创建两个 Provider
    ConsoleInputProvider({"user_nickname": "控制台用户A"})
    MockDanmakuProvider({})

    # 模拟同时发布多条消息
    raw_data1 = RawData(
        content="来自控制台的消息",
        source="console",
        data_type="text",
        metadata={"user": "控制台用户A", "user_id": "console_user"},
    )
    raw_data2 = RawData(
        content={"text": "来自弹幕的消息", "user_name": "粉丝B", "user_id": "111"},
        source="mock_danmaku",
        data_type="danmaku",
    )
    raw_data3 = RawData(
        content={"user_name": "用户C", "gift_name": "礼物D", "count": 1}, source="console", data_type="gift"
    )

    # 并发发布
    await asyncio.gather(
        event_bus.emit("perception.raw_data.generated", RawDataPayload.from_raw_data(raw_data1), source="Console"),
        event_bus.emit("perception.raw_data.generated", RawDataPayload.from_raw_data(raw_data2), source="Danmaku"),
        event_bus.emit("perception.raw_data.generated", RawDataPayload.from_raw_data(raw_data3), source="Console"),
    )

    await asyncio.sleep(0.3)

    # 验证所有消息都被处理
    assert len(collected_messages) == 3

    # 验证消息来源
    sources = [msg.source for msg in collected_messages]
    assert "console" in sources
    assert "mock_danmaku" in sources

    await input_coordinator.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
