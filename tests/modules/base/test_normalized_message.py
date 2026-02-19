"""
NormalizedMessage 单元测试

测试 NormalizedMessage 数据类的所有核心功能：
- 实例创建和基本属性
- 类型化字段（user_id, user_nickname, platform, room_id）
- display_text 属性
- from_raw_data 工厂方法
- 任意类型支持（raw 字段）
- 重要性计算
- 序列化

运行: uv run pytest tests/modules/base/test_normalized_message.py -v
"""

import time

import pytest

from src.modules.types.base.normalized_message import NormalizedMessage

# =============================================================================
# 测试 Mock 类
# =============================================================================


class MockStructuredContent:
    """模拟 StructuredContent 类（避免循环导入）"""

    def __init__(self, text: str, user_id: str = "test_user"):
        self._text = text
        self._user_id = user_id
        self.open_id = user_id  # 添加 open_id 属性以匹配 raw 对象
        self.type = "text"

    def get_display_text(self) -> str:
        """获取显示文本"""
        return self._text

    def get_user_id(self) -> str:
        """获取用户ID"""
        return self._user_id

    def model_dump(self) -> dict:
        """模拟 Pydantic 模型的序列化方法"""
        return {
            "text": self._text,
            "user_id": self._user_id,
            "type": self.type,
        }


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
        raw=mock_content,
        source="console",
        data_type="text",
        importance=0.8,
        user_id="user123",
        user_nickname="测试用户",
        platform="console",
        room_id="room001",
    )


# =============================================================================
# 实例创建和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_normalized_message_creation(mock_content):
    """测试创建 NormalizedMessage 实例"""
    message = NormalizedMessage(
        text="测试消息",
        raw=mock_content,
        source="bili_danmaku",
        data_type="text",
        importance=0.5,
    )

    assert message.text == "测试消息"
    assert message.raw == mock_content
    assert message.source == "bili_danmaku"
    assert message.data_type == "text"
    assert message.importance == 0.5
    assert message.timestamp == 0.0  # 默认值为 0.0


@pytest.mark.asyncio
async def test_normalized_message_with_explicit_timestamp(mock_content):
    """测试显式提供 timestamp"""
    custom_timestamp = time.time()
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        timestamp=custom_timestamp,
    )

    assert message.timestamp == custom_timestamp


@pytest.mark.asyncio
async def test_normalized_message_with_typed_fields(mock_content):
    """测试类型化字段"""
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        user_id="user456",
        user_nickname="测试昵称",
        platform="bilibili",
        room_id="123456",
    )

    assert message.user_id == "user456"
    assert message.user_nickname == "测试昵称"
    assert message.platform == "bilibili"
    assert message.room_id == "123456"


# =============================================================================
# display_text 属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_display_text_property():
    """测试 display_text 属性"""
    mock_content = MockStructuredContent("显示的文本内容", "user123")
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.display_text == "显示的文本内容"


@pytest.mark.asyncio
async def test_display_text_fallback_to_text():
    """测试 raw 为 None 时 display_text 回退到 text"""
    message = NormalizedMessage(
        text="回退文本",
        raw=None,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.display_text == "回退文本"


@pytest.mark.asyncio
async def test_display_text_fallback_when_no_method():
    """测试 raw 没有 get_display_text 方法时回退到 text"""

    class ContentWithoutMethod:
        """没有 get_display_text 方法的 content"""

        type = "test"

    content = ContentWithoutMethod()
    message = NormalizedMessage(
        text="回退文本",
        raw=content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.display_text == "回退文本"


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
        "user_id": "user789",
        "user_nickname": "原始用户",
        "platform": "bilibili",
        "room_id": "654321",
    }

    mock_content = MockStructuredContent("标准化文本", "user123")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data_dict,
        text="标准化文本",
        source="normalized_source",
        raw=mock_content,
        importance=0.9,
    )

    assert message.text == "标准化文本"
    assert message.raw == mock_content
    assert message.source == "normalized_source"
    assert message.data_type == "text"
    assert message.importance == 0.9
    # 类型化字段应该从 raw_data 中提取
    assert message.user_id == "user789"
    assert message.user_nickname == "原始用户"
    assert message.platform == "bilibili"
    assert message.room_id == "654321"


@pytest.mark.asyncio
async def test_from_raw_data_with_partial_dict():
    """测试从部分字典创建 NormalizedMessage（缺少某些字段）"""
    raw_data_dict = {
        "content": "原始内容",
        "user_id": "user_partial",
        # 缺少 user_nickname, platform, room_id
    }

    mock_content = MockStructuredContent("标准化文本", "user123")
    message = NormalizedMessage.from_raw_data(
        raw_data=raw_data_dict,
        text="标准化文本",
        source="test",
        raw=mock_content,
    )

    assert message.user_id == "user_partial"
    assert message.user_nickname is None
    assert message.platform is None
    assert message.room_id is None


# =============================================================================
# 任意类型支持测试
# =============================================================================


@pytest.mark.asyncio
async def test_raw_arbitrary_object():
    """测试 raw 字段支持任意对象"""
    custom_obj = MockStructuredContent("自定义对象", "user999")
    message = NormalizedMessage(
        text="测试",
        raw=custom_obj,
        source="test",
        data_type="custom",
        importance=0.5,
    )

    assert message.raw == custom_obj
    assert message.raw.get_display_text() == "自定义对象"


@pytest.mark.asyncio
async def test_raw_none():
    """测试 raw 可以为 None"""
    message = NormalizedMessage(
        text="测试",
        raw=None,
        source="test",
        data_type="none",
        importance=0.5,
    )

    assert message.raw is None


@pytest.mark.asyncio
async def test_raw_dict_object():
    """测试 raw 字段支持字典对象"""

    class DictContent:
        """模拟字典类型的 raw"""

        def __init__(self):
            self.data = {"key": "value"}

        def get_display_text(self) -> str:
            return str(self.data)

        def get_user_id(self) -> str:
            return "dict_user"

        type = "dict_type"

        def model_dump(self) -> dict:
            return self.data

    dict_content = DictContent()
    message = NormalizedMessage(
        text="测试",
        raw=dict_content,
        source="test",
        data_type="dict_type",
        importance=0.5,
    )

    assert message.raw.data == {"key": "value"}
    assert message.display_text == "{'key': 'value'}"


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
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.0,
    )
    assert low_message.importance == 0.0

    # 高重要性
    high_message = NormalizedMessage(
        text="高",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=1.0,
    )
    assert high_message.importance == 1.0

    # 中等重要性
    medium_message = NormalizedMessage(
        text="中",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )
    assert medium_message.importance == 0.5


# =============================================================================
# 序列化测试
# =============================================================================


@pytest.mark.asyncio
async def test_normalized_message_to_dict(mock_content):
    """测试 NormalizedMessage 的 to_dict 方法"""
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.8,
        user_id="user123",
        user_nickname="测试用户",
        platform="bilibili",
        room_id="123456",
        timestamp=1234567890.0,
    )

    serialized = message.to_dict()

    assert serialized["text"] == "测试"
    assert serialized["source"] == "test"
    assert serialized["data_type"] == "text"
    assert serialized["importance"] == 0.8
    assert serialized["timestamp"] == 1234567890.0
    assert serialized["user_id"] == "user123"
    assert serialized["user_nickname"] == "测试用户"
    assert serialized["platform"] == "bilibili"
    assert serialized["room_id"] == "123456"
    assert "raw_data" in serialized


@pytest.mark.asyncio
async def test_normalized_message_to_dict_without_raw():
    """测试 NormalizedMessage 的 to_dict 方法（无 raw）"""
    message = NormalizedMessage(
        text="测试",
        raw=None,
        source="test",
        data_type="text",
        importance=0.8,
        user_id="user123",
    )

    serialized = message.to_dict()

    assert serialized["text"] == "测试"
    assert serialized["user_id"] == "user123"
    assert "raw_data" not in serialized


@pytest.mark.asyncio
async def test_normalized_message_model_dump(mock_content):
    """测试 NormalizedMessage 的 model_dump 方法（Pydantic 原生）"""
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.8,
        user_id="user123",
        user_nickname="测试用户",
        platform="bilibili",
        room_id="123456",
    )

    serialized = message.model_dump()

    assert serialized["text"] == "测试"
    assert serialized["source"] == "test"
    assert serialized["data_type"] == "text"
    assert serialized["importance"] == 0.8
    assert serialized["user_id"] == "user123"
    assert serialized["user_nickname"] == "测试用户"
    assert serialized["platform"] == "bilibili"
    assert serialized["room_id"] == "123456"
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
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
    )

    assert message.text == ""
    assert message.display_text == ""


@pytest.mark.asyncio
async def test_zero_timestamp_remains_zero():
    """测试 timestamp 默认为 0.0 且保持不变"""
    mock_content = MockStructuredContent("测试", "user123")
    message = NormalizedMessage(
        text="测试",
        raw=mock_content,
        source="test",
        data_type="text",
        importance=0.5,
        # 不提供 timestamp，使用默认值 0.0
    )

    # timestamp 应该保持默认值 0.0（不再自动设置）
    assert message.timestamp == 0.0


@pytest.mark.asyncio
async def test_optional_fields_default_to_none():
    """测试可选字段默认为 None"""
    message = NormalizedMessage(
        text="测试",
        source="test",
    )

    assert message.raw is None
    assert message.user_id is None
    assert message.user_nickname is None
    assert message.platform is None
    assert message.room_id is None


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
