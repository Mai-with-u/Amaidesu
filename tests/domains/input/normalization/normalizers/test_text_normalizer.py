"""
测试 TextNormalizer（pytest）

运行: uv run pytest tests/domains/normalization/test_text_normalizer.py -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from unittest.mock import AsyncMock, Mock

import pytest

from src.domains.input.normalization.normalizers.text_normalizer import TextNormalizer
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.raw_data import RawData

# =============================================================================
# 初始化测试
# =============================================================================


def test_text_normalizer_init_without_pipeline():
    """测试 TextNormalizer 初始化（不使用 InputPipelineManager）"""
    normalizer = TextNormalizer()

    assert normalizer is not None
    assert normalizer.pipeline_manager is None
    assert normalizer.can_handle("text") is True
    assert normalizer.can_handle("gift") is False
    assert normalizer.priority == 100


def test_text_normalizer_init_with_pipeline():
    """测试 TextNormalizer 初始化（使用 InputPipelineManager）"""
    mock_pipeline = Mock()

    normalizer = TextNormalizer(pipeline_manager=mock_pipeline)

    assert normalizer is not None
    assert normalizer.pipeline_manager is mock_pipeline


# =============================================================================
# 文本数据转换测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_with_string_content():
    """测试处理字符串内容"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="Hello, Amaidesu!", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert isinstance(result, NormalizedMessage)
    assert result.text == "Hello, Amaidesu!"
    assert result.source == "test"
    assert result.data_type == "text"
    assert result.importance == 0.3  # TextContent 的默认重要性
    assert result.content.type == "text"


@pytest.mark.asyncio
async def test_text_normalizer_with_dict_content():
    """测试处理字典格式内容"""
    normalizer = TextNormalizer()

    raw_data = RawData(content={"text": "Hello from dict!"}, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "Hello from dict!"
    assert result.content.type == "text"


@pytest.mark.asyncio
async def test_text_normalizer_with_dict_without_text_key():
    """测试处理字典格式内容（没有 text 键）"""
    normalizer = TextNormalizer()

    # 使用其他键（非 message），确保不会触发 MessageBase 逻辑
    raw_data = RawData(content={"data": "Hello without text key"}, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # 应该使用字典的字符串表示（因为没有 text 或 message 键）
    assert "data" in result.text


@pytest.mark.asyncio
async def test_text_normalizer_metadata_handling():
    """测试元数据处理"""
    normalizer = TextNormalizer()

    raw_data = RawData(
        content="Test message",
        source="bili_danmaku",
        data_type="text",
        metadata={"user": "test_user", "user_id": "12345"},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.metadata["user"] == "test_user"
    assert result.metadata["user_id"] == "12345"
    assert result.metadata["source"] == "bili_danmaku"
    assert "original_timestamp" in result.metadata


# =============================================================================
# InputPipelineManager 集成测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_with_pipeline_processing():
    """测试使用 InputPipelineManager 处理文本"""
    mock_pipeline = Mock()
    mock_pipeline.process_text = AsyncMock(return_value="Processed: Hello")

    normalizer = TextNormalizer(pipeline_manager=mock_pipeline)

    raw_data = RawData(content="Hello", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "Processed: Hello"
    mock_pipeline.process_text.assert_called_once()


@pytest.mark.asyncio
async def test_text_normalizer_with_pipeline_returns_none():
    """测试 InputPipelineManager 返回 None（应该丢弃消息）"""
    mock_pipeline = Mock()
    mock_pipeline.process_text = AsyncMock(return_value=None)

    normalizer = TextNormalizer(pipeline_manager=mock_pipeline)

    raw_data = RawData(content="To be filtered", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is None
    mock_pipeline.process_text.assert_called_once()


@pytest.mark.asyncio
async def test_text_normalizer_with_pipeline_error():
    """测试 InputPipelineManager 抛出异常（应该使用原文本）"""
    mock_pipeline = Mock()
    mock_pipeline.process_text = AsyncMock(side_effect=Exception("Pipeline error"))

    normalizer = TextNormalizer(pipeline_manager=mock_pipeline)

    raw_data = RawData(content="Original text", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    # 出错时应该使用原文本
    assert result is not None
    assert result.text == "Original text"
    mock_pipeline.process_text.assert_called_once()


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_empty_text():
    """测试空文本"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == ""
    assert result.content.text == ""


@pytest.mark.asyncio
async def test_text_normalizer_very_long_text():
    """测试超长文本"""
    normalizer = TextNormalizer()

    long_text = "A" * 10000

    raw_data = RawData(content=long_text, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == long_text
    assert len(result.text) == 10000


@pytest.mark.asyncio
async def test_text_normalizer_unicode_text():
    """测试 Unicode 文本"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="你好世界 🎉 Test 测试", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "你好世界" in result.text
    assert "🎉" in result.text
    assert "测试" in result.text


@pytest.mark.asyncio
async def test_text_normalizer_numeric_content():
    """测试数字内容"""
    normalizer = TextNormalizer()

    raw_data = RawData(content=12345, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "12345"


@pytest.mark.asyncio
async def test_text_normalizer_none_content():
    """测试 None 内容"""
    normalizer = TextNormalizer()

    raw_data = RawData(content=None, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "None"


@pytest.mark.asyncio
async def test_text_normalizer_multiline_text():
    """测试多行文本"""
    normalizer = TextNormalizer()

    multiline_text = """Line 1
Line 2
Line 3"""

    raw_data = RawData(content=multiline_text, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "Line 1" in result.text
    assert "Line 2" in result.text
    assert "Line 3" in result.text


# =============================================================================
# TextContent 相关测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_content_get_importance():
    """测试 TextContent 的重要性计算"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="Test", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # TextContent 的重要性应该是 0.3
    assert result.importance == 0.3


@pytest.mark.asyncio
async def test_text_content_get_display_text():
    """测试 TextContent 的显示文本"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="Display this text", source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.get_display_text() == "Display this text"
    assert result.text == "Display this text"


# =============================================================================
# Timestamp 测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_preserves_timestamp():
    """测试保留原始时间戳"""
    normalizer = TextNormalizer()

    raw_data = RawData(content="Test", source="test", data_type="text", metadata={}, timestamp=1234567890.0)

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.timestamp == 1234567890.0
    assert result.metadata["original_timestamp"] == 1234567890.0


# =============================================================================
# 不同数据源测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_different_sources():
    """测试不同数据源"""
    normalizer = TextNormalizer()

    sources = ["console", "bili_danmaku", "minecraft", "test_source"]

    for source in sources:
        raw_data = RawData(content=f"Message from {source}", source=source, data_type="text", metadata={})

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.source == source
        assert result.metadata["source"] == source


# =============================================================================
# MessageBase 格式测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_normalizer_with_message_base_format():
    """测试处理 MessageBase 格式（来自 BiliDanmakuOfficialInputProvider）"""
    from unittest.mock import MagicMock

    normalizer = TextNormalizer()

    # 创建模拟的 MessageBase 对象
    mock_message_base = MagicMock()
    mock_message_base.raw_message = "弹幕消息内容"
    mock_message_base.message_info.user_info.user_id = "test_user_123"
    mock_message_base.message_info.user_info.user_nickname = "测试用户"
    mock_message_base.message_info.group_info.group_id = "test_group_456"
    mock_message_base.message_info.group_info.group_name = "测试群组"

    raw_data = RawData(
        content={"message": mock_message_base, "message_config": {}},
        source="bili_danmaku_official",
        data_type="text",
        metadata={"message_id": "msg_001", "room_id": "room_123"},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "弹幕消息内容"
    assert result.metadata["user_id"] == "test_user_123"
    assert result.metadata["user_nickname"] == "测试用户"
    assert result.metadata["group_id"] == "test_group_456"
    assert result.metadata["group_name"] == "测试群组"
    assert result.metadata["message_id"] == "msg_001"
    assert result.metadata["room_id"] == "room_123"
    assert result.metadata["source"] == "bili_danmaku_official"


@pytest.mark.asyncio
async def test_text_normalizer_with_message_base_string_value():
    """测试处理 dict 中 message 键为字符串的情况（降级处理）"""
    normalizer = TextNormalizer()

    # message 值是字符串（不是 MessageBase 对象）
    raw_data = RawData(content={"message": "Hello from message key"}, source="test", data_type="text", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # 应该使用字符串值本身（str() 对字符串返回相同值）
    assert result.text == "Hello from message key"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
