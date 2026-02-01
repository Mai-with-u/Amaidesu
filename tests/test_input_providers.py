"""
测试 InputLayer 和 InputProvider（pytest 标准）

使用 pytest 和 pytest-asyncio 测试输入层功能。
运行: uv run pytest tests/test_input_providers.py -v
"""

import asyncio
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.layers.input.input_layer import InputLayer
from src.layers.input.providers.console_input_provider import ConsoleInputProvider
from src.layers.input.providers.mock_danmaku_provider import MockDanmakuProvider


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def event_bus():
    """创建事件总线"""
    bus = EventBus()
    yield bus
    # 清理（如果需要）


@pytest.fixture
async def input_layer(event_bus):
    """创建 InputLayer"""
    layer = InputLayer(event_bus)
    await layer.setup()
    yield layer
    await layer.cleanup()


# =============================================================================
# InputLayer 测试
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

    # 监听消息就绪事件
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


# =============================================================================
# ConsoleInputProvider 测试
# =============================================================================

@pytest.mark.asyncio
async def test_console_input_provider_init():
    """测试 ConsoleInputProvider 初始化"""
    config = {
        "user_id": "test_user",
        "user_nickname": "测试用户"
    }

    provider = ConsoleInputProvider(config)

    assert provider is not None
    assert provider.user_id == "test_user"
    assert provider.user_nickname == "测试用户"
    assert not provider._running  # 使用私有属性检查初始状态


@pytest.mark.asyncio
async def test_console_input_provider_config_defaults():
    """测试 ConsoleInputProvider 默认配置"""
    provider = ConsoleInputProvider({})

    assert provider.user_id == "console_user"
    assert provider.user_nickname == "控制台"


# =============================================================================
# MockDanmakuProvider 测试
# =============================================================================

@pytest.mark.asyncio
async def test_mock_danmaku_provider_init():
    """测试 MockDanmakuProvider 初始化"""
    config = {
        "interval": 5,
        "messages": ["消息1", "消息2"]
    }

    provider = MockDanmakuProvider(config)

    assert provider is not None
    assert not provider.is_running


# =============================================================================
# 集成测试
# =============================================================================

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


# =============================================================================
# 运行测试的主函数（可选）
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
