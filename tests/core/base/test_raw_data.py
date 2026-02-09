"""
RawData 单元测试

测试 RawData 数据类的所有核心功能：
- 实例创建和基本属性
- 默认值验证（timestamp 自动生成）
- 序列化和反序列化
- 元数据扩展
- preserve_original 和 original_data 字段
- 任意类型支持（content 字段）

运行: uv run pytest tests/core/base/test_raw_data.py -v
"""

import time
import json
from typing import Dict, Any

import pytest
from pydantic import ValidationError

from src.core.base.raw_data import RawData


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def sample_raw_data():
    """创建标准的 RawData 实例"""
    return RawData(
        content="测试消息内容",
        source="console",
        data_type="text",
        metadata={"user": "测试用户", "user_id": "user123"},
    )


# =============================================================================
# 实例创建和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_creation():
    """测试创建 RawData 实例"""
    raw_data = RawData(
        content="测试消息",
        source="bili_danmaku",
        data_type="text",
    )

    assert raw_data.content == "测试消息"
    assert raw_data.source == "bili_danmaku"
    assert raw_data.data_type == "text"
    assert isinstance(raw_data.timestamp, float)
    assert raw_data.preserve_original is False
    assert raw_data.original_data is None
    assert isinstance(raw_data.metadata, dict)


@pytest.mark.asyncio
async def test_raw_data_creation_with_all_fields():
    """测试创建包含所有字段的 RawData 实例"""
    custom_timestamp = time.time()
    raw_data = RawData(
        content="完整消息",
        source="minecraft",
        data_type="event",
        timestamp=custom_timestamp,
        preserve_original=True,
        original_data={"raw": "原始数据"},
        metadata={"level": "info", "event": "player_join"},
    )

    assert raw_data.content == "完整消息"
    assert raw_data.source == "minecraft"
    assert raw_data.data_type == "event"
    assert raw_data.timestamp == custom_timestamp
    assert raw_data.preserve_original is True
    assert raw_data.original_data == {"raw": "原始数据"}
    assert raw_data.metadata == {"level": "info", "event": "player_join"}


# =============================================================================
# 默认值验证测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_timestamp_default():
    """测试 timestamp 默认为当前时间"""
    before = time.time()
    raw_data = RawData(content="消息", source="test", data_type="text")
    after = time.time()

    # timestamp 应该在创建前后时间之间
    assert before <= raw_data.timestamp <= after


@pytest.mark.asyncio
async def test_raw_data_preserve_original_default():
    """测试 preserve_original 默认为 False"""
    raw_data = RawData(content="消息", source="test", data_type="text")
    assert raw_data.preserve_original is False


@pytest.mark.asyncio
async def test_raw_data_original_data_default():
    """测试 original_data 默认为 None"""
    raw_data = RawData(content="消息", source="test", data_type="text")
    assert raw_data.original_data is None


@pytest.mark.asyncio
async def test_raw_data_metadata_default():
    """测试 metadata 默认为空字典"""
    raw_data = RawData(content="消息", source="test", data_type="text")
    assert raw_data.metadata == {}
    assert isinstance(raw_data.metadata, dict)


# =============================================================================
# 序列化和反序列化测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_serialization():
    """测试 RawData 序列化为字典"""
    raw_data = RawData(
        content="序列化测试",
        source="test",
        data_type="text",
        metadata={"key": "value"},
    )

    serialized = raw_data.model_dump()

    assert serialized["content"] == "序列化测试"
    assert serialized["source"] == "test"
    assert serialized["data_type"] == "text"
    assert serialized["metadata"] == {"key": "value"}
    assert "timestamp" in serialized


@pytest.mark.asyncio
async def test_raw_data_deserialization():
    """测试从字典反序列化 RawData"""
    data_dict = {
        "content": "反序列化测试",
        "source": "test",
        "data_type": "text",
        "timestamp": 1234567890.0,
        "preserve_original": False,
        "original_data": None,
        "metadata": {"key": "value"},
    }

    raw_data = RawData(**data_dict)

    assert raw_data.content == "反序列化测试"
    assert raw_data.source == "test"
    assert raw_data.data_type == "text"
    assert raw_data.timestamp == 1234567890.0
    assert raw_data.metadata == {"key": "value"}


@pytest.mark.asyncio
async def test_raw_data_json_serialization():
    """测试 RawData JSON 序列化"""
    raw_data = RawData(
        content="JSON 测试",
        source="test",
        data_type="text",
        metadata={"user": "测试"},
    )

    json_str = raw_data.model_dump_json()
    parsed = json.loads(json_str)

    assert parsed["content"] == "JSON 测试"
    assert parsed["source"] == "test"
    assert parsed["data_type"] == "text"
    assert parsed["metadata"]["user"] == "测试"


# =============================================================================
# 元数据扩展测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_metadata_extension():
    """测试元数据可以扩展和修改"""
    raw_data = RawData(
        content="消息",
        source="test",
        data_type="text",
        metadata={"key1": "value1"},
    )

    # 添加新的元数据
    raw_data.metadata["key2"] = "value2"
    raw_data.metadata["count"] = 42

    assert raw_data.metadata == {
        "key1": "value1",
        "key2": "value2",
        "count": 42,
    }


@pytest.mark.asyncio
async def test_raw_data_metadata_complex_types():
    """测试元数据支持复杂数据类型"""
    raw_data = RawData(
        content="消息",
        source="test",
        data_type="text",
        metadata={
            "list": [1, 2, 3],
            "nested": {"a": 1, "b": 2},
            "bool": True,
            "none": None,
        },
    )

    assert raw_data.metadata["list"] == [1, 2, 3]
    assert raw_data.metadata["nested"] == {"a": 1, "b": 2}
    assert raw_data.metadata["bool"] is True
    assert raw_data.metadata["none"] is None


# =============================================================================
# preserve_original 和 original_data 字段测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_preserve_original_flag():
    """测试 preserve_original 标志位"""
    raw_data = RawData(
        content="处理后内容",
        source="test",
        data_type="text",
        preserve_original=True,
        original_data="原始内容",
    )

    assert raw_data.preserve_original is True
    assert raw_data.original_data == "原始内容"


@pytest.mark.asyncio
async def test_raw_data_original_data_types():
    """测试 original_data 支持多种类型"""
    # 字符串
    raw_data1 = RawData(
        content="处理后",
        source="test",
        data_type="text",
        original_data="原始字符串",
    )
    assert raw_data1.original_data == "原始字符串"

    # 字典
    raw_data2 = RawData(
        content="处理后",
        source="test",
        data_type="text",
        original_data={"raw": "原始数据", "value": 123},
    )
    assert raw_data2.original_data == {"raw": "原始数据", "value": 123}

    # 列表
    raw_data3 = RawData(
        content="处理后",
        source="test",
        data_type="text",
        original_data=["item1", "item2"],
    )
    assert raw_data3.original_data == ["item1", "item2"]

    # None
    raw_data4 = RawData(
        content="处理后",
        source="test",
        data_type="text",
        original_data=None,
    )
    assert raw_data4.original_data is None


# =============================================================================
# 任意类型支持测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_content_string():
    """测试 content 字段支持字符串"""
    raw_data = RawData(content="文本消息", source="test", data_type="text")
    assert raw_data.content == "文本消息"
    assert isinstance(raw_data.content, str)


@pytest.mark.asyncio
async def test_raw_data_content_dict():
    """测试 content 字段支持字典"""
    content_dict = {
        "user_name": "张三",
        "gift_name": "小星星",
        "count": 10,
    }
    raw_data = RawData(content=content_dict, source="test", data_type="gift")

    assert raw_data.content == content_dict
    assert raw_data.content["user_name"] == "张三"
    assert raw_data.content["gift_name"] == "小星星"
    assert raw_data.content["count"] == 10


@pytest.mark.asyncio
async def test_raw_data_content_bytes():
    """测试 content 字段支持字节"""
    content_bytes = b"binary data"
    raw_data = RawData(content=content_bytes, source="test", data_type="audio")

    assert raw_data.content == content_bytes
    assert isinstance(raw_data.content, bytes)


@pytest.mark.asyncio
async def test_raw_data_content_list():
    """测试 content 字段支持列表"""
    content_list = ["item1", "item2", "item3"]
    raw_data = RawData(content=content_list, source="test", data_type="batch")

    assert raw_data.content == content_list
    assert len(raw_data.content) == 3


@pytest.mark.asyncio
async def test_raw_data_content_number():
    """测试 content 字段支持数字"""
    raw_data = RawData(content=42, source="test", data_type="number")
    assert raw_data.content == 42
    assert isinstance(raw_data.content, int)


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_data_empty_content():
    """测试空 content"""
    raw_data = RawData(content="", source="test", data_type="text")
    assert raw_data.content == ""


@pytest.mark.asyncio
async def test_raw_data_empty_metadata():
    """测试空 metadata"""
    raw_data = RawData(content="消息", source="test", data_type="text", metadata={})
    assert raw_data.metadata == {}


@pytest.mark.asyncio
async def test_raw_data_special_characters_in_content():
    """测试 content 包含特殊字符"""
    special_content = "测试\n特殊\t字符😊emoji"
    raw_data = RawData(content=special_content, source="test", data_type="text")
    assert raw_data.content == special_content


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
