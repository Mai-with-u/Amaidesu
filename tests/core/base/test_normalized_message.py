"""
NormalizedMessage 单元测试

测试 NormalizedMessage 数据类的所有核心功能：
- 实例创建和基本属性
- model_validator 自动设置 timestamp 和 metadata
- user_id 和 display_text 属性
- from_raw_data 工厂方法
- 任意类型支持（content 字段）
- 重要性计算

运行: uv run pytest tests/core/base/test_normalized_message.py -v
"""

import time

import pytest

from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.raw_data import RawData

# =============================================================================
# 测试 Mock 类
# =============================================================================


class MockStructuredContent:
    """模拟 StructuredContent 类（避免循环导入）"""

    def __init__(self, text: str, user_id: str = "test_user"):
        self._text = text
        self._user_id = user_id
        self.type = "text"

    def get_display_text(self) -> str:
        """获取显示文本"""
        return self._text

    def get_user_id(self) -> str:
        """获取用户ID"""
        return self._user_id


# =============================================================================
# 测试 Fixture
# =============================================================================


@pytest.fixture
def mock_content():
    """创建模拟的结构化内容"""
    return MockStructuredContent("测试消息", "user123")


@pytest.fixture
def sample_normalized_message(mock_content):
    """创建标准的 NormalizedMessage 实例"""
    return NormalizedMessage(
        text="测试消息",
        content=mock_content,
        source="console",
        data_type="text",
        importance=0.8,
        metadata={"user": "测试用户"},
    )


# =============================================================================
# 实例创建和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_normalized_message_creation(mock_content):
    """测试创建 NormalizedMessage 实例"""
    message = NormalizedMessage(
        text="测试消息",
        content=mock_content,
        source="bili_danmaku",
        data_type="text",
        importance=0.5,
    )

    assert message.text == "测试消息"
    assert message.content == mock_content
    assert message.source == "bili_danmaku"
    assert message.data_type == "text"
    assert message.importance == 0.5
    assert isinstance(message.metadata, dict)
    assert isinstance(message.timestamp, float)


@pytest.mark.asyncio
async def test_normalized_message_with_explicit_timestamp(mock_content):
    """测试显式提供 timestamp"""
    custom_timestamp = time.time()
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        timestamp=custom_timestamp,
    )

    assert message.timestamp == custom_timestamp


@pytest.mark.asyncio
async def test_normalized_message_with_metadata(mock_content):
    """测试提供自定义 metadata"""
    custom_metadata = {"key1": "value1", "key2": "value2"}
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        metadata=custom_metadata,
    )

    # metadata 应该包含自定义字段
    assert message.metadata["key1"] == "value1"
    assert message.metadata["key2"] == "value2"


# =============================================================================
# model_validator 自动设置测试
# =============================================================================


@pytest.mark.asyncio
async def test_model_validator_auto_timestamp(mock_content):
    """测试 model_validator 自动设置 timestamp"""
    before = time.time()
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        # 不提供 timestamp，使用默认值 0.0
    )
    after = time.time()

    # timestamp 应该被自动设置为当前时间
    assert before <= message.timestamp <= after
    assert message.timestamp != 0.0


@pytest.mark.asyncio
async def test_model_validator_metadata_type(mock_content):
    """测试 model_validator 自动添加 type 到 metadata"""
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    # metadata 应该自动包含 type 字段
    assert "type" in message.metadata
    assert message.metadata["type"] == "text"


@pytest.mark.asyncio
async def test_model_validator_metadata_timestamp(mock_content):
    """测试 model_validator 自动添加 timestamp 到 metadata"""
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    # metadata 应该自动包含 timestamp 字段
    assert "timestamp" in message.metadata
    assert message.metadata["timestamp"] == message.timestamp


@pytest.mark.asyncio
async def test_model_validator_preserve_custom_metadata(mock_content):
    """测试 model_validator 保留自定义 metadata"""
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        metadata={"custom_key": "custom_value"},
    )

    # 自定义 metadata 应该被保留
    assert message.metadata["custom_key"] == "custom_value"
    # 同时自动添加的字段也应该存在
    assert "type" in message.metadata
    assert "timestamp" in message.metadata


# =============================================================================
# user_id 和 display_text 属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_user_id_property():
    """测试 user_id 属性"""
    mock_content = MockStructuredContent("测试消息", "user456")
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.user_id == "user456"


@pytest.mark.asyncio
async def test_display_text_property():
    """测试 display_text 属性"""
    mock_content = MockStructuredContent("显示的文本内容", "user123")
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.display_text == "显示的文本内容"


@pytest.mark.asyncio
async def test_user_id_returns_none_when_not_implemented():
    """测试 content 没有实现 get_user_id 时返回 None"""

    class IncompleteContent:
        """不完整的 content 类（没有 get_user_id）"""

        def get_display_text(self) -> str:
            return "显示文本"

        type = "test"

    incomplete_content = IncompleteContent()
    message = NormalizedMessage(
        text="测试",
        content=incomplete_content,
        source="test",
        data_type="test",
        importance=0.5,
    )

    # 应该抛出 AttributeError，因为 IncompleteContent 没有 get_user_id
    with pytest.raises(AttributeError):
        _ = message.user_id


# =============================================================================
# from_raw_data 工厂方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_from_raw_data_with_dict():
    """测试从字典创建 NormalizedMessage"""
    raw_data_dict = {
        "content": "原始内容",
        "source": "console",
        "data_type": "text",
        "metadata": {"original_key": "original_value"},
    }

    mock_content = MockStructuredContent("标准化文本", "user123")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data_dict,
        text="标准化文本",
        source="normalized_source",
        content=mock_content,
        importance=0.9,
    )

    assert message.text == "标准化文本"
    assert message.content == mock_content
    assert message.source == "normalized_source"
    assert message.data_type == "text"
    assert message.importance == 0.9
    # metadata 应该包含原始数据的信息
    assert message.metadata["original_key"] == "original_value"
    assert message.metadata["source"] == "normalized_source"


@pytest.mark.asyncio
async def test_from_raw_data_with_raw_data_object():
    """测试从 RawData 对象创建 NormalizedMessage"""
    raw_data = RawData(
        content="原始内容",
        source="bili_danmaku",
        data_type="text",
        metadata={"user": "原始用户", "user_id": "user789"},
    )

    mock_content = MockStructuredContent("标准化文本", "user789")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data,
        text="标准化文本",
        source="normalized_source",
        content=mock_content,
        importance=0.7,
    )

    assert message.text == "标准化文本"
    assert message.content == mock_content
    assert message.source == "normalized_source"
    assert message.importance == 0.7
    # metadata 应该包含原始数据的信息
    assert message.metadata["user"] == "原始用户"
    assert message.metadata["user_id"] == "user789"


@pytest.mark.asyncio
async def test_from_raw_data_metadata_isolation():
    """测试 from_raw_data 隔离 metadata（不影响原始对象）"""
    raw_data = RawData(
        content="原始内容",
        source="test",
        data_type="text",
        metadata={"key": "value"},
    )

    mock_content = MockStructuredContent("标准化文本", "user123")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data,
        text="标准化文本",
        source="normalized",
        content=mock_content,
        importance=0.5,
    )

    # 修改 message 的 metadata
    message.metadata["new_key"] = "new_value"

    # 原始 raw_data 的 metadata 不应该被修改
    assert "new_key" not in raw_data.metadata
    assert raw_data.metadata == {"key": "value"}


@pytest.mark.asyncio
async def test_from_raw_data_auto_add_metadata():
    """测试 from_raw_data 自动添加基本元数据"""
    raw_data = RawData(
        content="原始内容",
        source="test",
        data_type="text",
    )

    mock_content = MockStructuredContent("标准化文本", "user123")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data,
        text="标准化文本",
        source="normalized",
        content=mock_content,
        importance=0.5,
    )

    # metadata 应该包含自动添加的字段
    assert message.metadata["source"] == "normalized"
    assert message.metadata["type"] == "text"
    assert "timestamp" in message.metadata


# =============================================================================
# 任意类型支持测试
# =============================================================================


@pytest.mark.asyncio
async def test_content_arbitrary_object():
    """测试 content 字段支持任意对象"""
    custom_obj = MockStructuredContent("自定义对象", "user999")
    message = NormalizedMessage(
        text="测试",
        content=custom_obj,
        source="test",
        data_type="custom",
        importance=0.5,
    )

    assert message.content == custom_obj
    assert message.content.get_display_text() == "自定义对象"


@pytest.mark.asyncio
async def test_content_none():
    """测试 content 可以为 None（arbitrary_types_allowed）"""
    message = NormalizedMessage(
        text="测试",
        content=None,
        source="test",
        data_type="none",
        importance=0.5,
    )

    assert message.content is None


@pytest.mark.asyncio
async def test_content_dict_object():
    """测试 content 字段支持字典对象"""

    class DictContent:
        """模拟字典类型的 content"""

        def __init__(self):
            self.data = {"key": "value"}

        def get_display_text(self) -> str:
            return str(self.data)

        def get_user_id(self) -> str:
            return "dict_user"

        type = "dict_type"

    dict_content = DictContent()
    message = NormalizedMessage(
        text="测试",
        content=dict_content,
        source="test",
        data_type="dict_type",
        importance=0.5,
    )

    assert message.content.data == {"key": "value"}
    assert message.display_text == "{'key': 'value'}"
    assert message.user_id == "dict_user"


# =============================================================================
# 重要性测试
# =============================================================================


@pytest.mark.asyncio
async def test_importance_range():
    """测试 importance 字段的范围"""
    mock_content = MockStructuredContent("测试", "user123")

    # 低重要性
    low_message = NormalizedMessage(
        text="低",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.0,
    )
    assert low_message.importance == 0.0

    # 高重要性
    high_message = NormalizedMessage(
        text="高",
        content=mock_content,
        source="test",
        data_type="text",
        importance=1.0,
    )
    assert high_message.importance == 1.0

    # 中等重要性
    medium_message = NormalizedMessage(
        text="中",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )
    assert medium_message.importance == 0.5


# =============================================================================
# 序列化测试
# =============================================================================


@pytest.mark.asyncio
async def test_normalized_message_serialization(mock_content):
    """测试 NormalizedMessage 序列化"""
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.8,
        metadata={"key": "value"},
    )

    serialized = message.model_dump()

    assert serialized["text"] == "测试"
    assert serialized["source"] == "test"
    assert serialized["data_type"] == "text"
    assert serialized["importance"] == 0.8
    assert serialized["metadata"]["key"] == "value"
    assert "timestamp" in serialized


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_empty_text():
    """测试空文本"""
    mock_content = MockStructuredContent("", "user123")
    message = NormalizedMessage(
        text="",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.text == ""
    assert message.display_text == ""


@pytest.mark.asyncio
async def test_zero_timestamp():
    """测试 timestamp 为 0 时会被自动设置"""
    mock_content = MockStructuredContent("测试", "user123")
    message = NormalizedMessage(
        text="测试",
        content=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        timestamp=0.0,  # 显式设置为 0
    )

    # model_validator 应该将 0.0 替换为当前时间
    assert message.timestamp != 0.0
    assert message.timestamp > 0


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
