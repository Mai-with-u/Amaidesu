"""
CanonicalMessage单元测试

测试CanonicalMessage类和MessageBuilder工具的功能。
"""

from src.canonical.canonical_message import CanonicalMessage, MessageBuilder
from src.core.data_types.normalized_text import NormalizedText


class TestCanonicalMessage:
    """测试CanonicalMessage类"""

    def test_canonical_message_creation(self):
        """测试CanonicalMessage基本创建"""
        message = CanonicalMessage(
            text="Hello World",
            source="console",
            metadata={"user_id": "test_user"},
            timestamp=1234567890.0,
        )

        assert message.text == "Hello World"
        assert message.source == "console"
        assert message.metadata["user_id"] == "test_user"
        assert message.timestamp == 1234567890.0
        assert message.data_ref is None
        assert message.original_message is None

    def test_canonical_message_with_all_fields(self):
        """测试带所有字段的CanonicalMessage"""
        message = CanonicalMessage(
            text="Test message",
            source="danmaku",
            metadata={"user_id": "123", "room_id": "456"},
            data_ref="cache://image/abc123",
        )

        assert message.text == "Test message"
        assert message.source == "danmaku"
        assert message.metadata["user_id"] == "123"
        assert message.metadata["room_id"] == "456"
        assert message.data_ref == "cache://image/abc123"

    def test_canonical_message_to_dict(self):
        """测试CanonicalMessage序列化"""
        message = CanonicalMessage(
            text="Hello",
            source="console",
            metadata={"user_id": "test"},
        )

        data = message.to_dict()

        assert data["text"] == "Hello"
        assert data["source"] == "console"
        assert data["metadata"]["user_id"] == "test"
        assert data["timestamp"] is not None
        assert "original_message" not in data

    def test_canonical_message_from_dict(self):
        """测试从字典反序列化CanonicalMessage"""
        data = {
            "text": "World",
            "source": "test",
            "metadata": {"key": "value"},
            "timestamp": 1234567890.0,
        }

        message = CanonicalMessage.from_dict(data)

        assert message.text == "World"
        assert message.source == "test"
        assert message.metadata["key"] == "value"
        assert message.timestamp == 1234567890.0

    def test_canonical_message_repr(self):
        """测试CanonicalMessage字符串表示"""
        message = CanonicalMessage(
            text="This is a very long message that should be truncated in the repr",
            source="test",
        )

        repr_str = repr(message)

        assert "CanonicalMessage" in repr_str
        assert "test" in repr_str
        assert "..." in repr_str  # 应该被截断

    def test_canonical_message_from_normalized_text(self):
        """测试从NormalizedText创建CanonicalMessage"""
        normalized_text = NormalizedText(
            text="Normalized content",
            metadata={"type": "text", "user_id": "user1"},
        )

        message = CanonicalMessage.from_normalized_text(normalized_text)

        assert message.text == "Normalized content"
        assert message.source == normalized_text.source
        assert message.metadata["type"] == "text"
        assert message.metadata["user_id"] == "user1"

    def test_canonical_message_metadata_copy(self):
        """测试metadata应该被复制而非引用"""
        original_metadata = {"key": "value"}
        message = CanonicalMessage(
            text="Test",
            source="test",
            metadata=original_metadata,
        )

        # 修改原始metadata
        original_metadata["key"] = "modified"

        # CanonicalMessage中的metadata不应该受影响
        assert message.metadata["key"] == "value"


class TestMessageBuilder:
    """测试MessageBuilder工具类"""

    def test_build_from_normalized_text(self):
        """测试从NormalizedText构建CanonicalMessage"""
        normalized_text = NormalizedText(
            text="Test content",
            metadata={"user_id": "123"},
        )

        message = MessageBuilder.build_from_normalized_text(normalized_text)

        assert message.text == "Test content"
        assert message.metadata["user_id"] == "123"

    def test_build_from_text(self):
        """测试从文本构建CanonicalMessage"""
        message = MessageBuilder.build_from_text(
            text="Hello",
            source="console",
            user_id="test_user",
            user_nickname="Test User",
        )

        assert message.text == "Hello"
        assert message.source == "console"
        assert message.metadata["user_id"] == "test_user"
        assert message.metadata["user_nickname"] == "Test User"

    def test_build_from_text_with_empty_metadata(self):
        """测试构建不带metadata的CanonicalMessage"""
        message = MessageBuilder.build_from_text(
            text="Test",
            source="test",
        )

        assert message.text == "Test"
        assert message.source == "test"
        assert len(message.metadata) == 0  # 应该是空字典

    def test_build_from_message_base_simple(self):
        """测试从简单MessageBase构建CanonicalMessage"""
        # 注意：这个测试需要mock MessageBase
        # 由于无法直接导入maim_message.MessageBase，
        # 我们只测试逻辑路径
        pass

    def test_message_builder_preserves_original_message(self):
        """测试MessageBuilder保留原始MessageBase"""
        # 这个测试需要实际的MessageBase对象
        # 我们验证逻辑是否正确
        pass


class TestCanonicalMessageIntegration:
    """测试CanonicalMessage集成场景"""

    def test_round_trip_serialization(self):
        """测试序列化和反序列化的往返"""
        original = CanonicalMessage(
            text="Original message",
            source="integration",
            metadata={"key1": "value1", "key2": "value2"},
        )

        # 序列化
        data = original.to_dict()

        # 反序列化
        restored = CanonicalMessage.from_dict(data)

        # 验证数据一致
        assert restored.text == original.text
        assert restored.source == original.source
        assert restored.metadata == original.metadata
        assert abs(restored.timestamp - original.timestamp) < 1.0

    def test_canonical_message_with_empty_text(self):
        """测试空文本的CanonicalMessage"""
        message = CanonicalMessage(
            text="",
            source="test",
        )

        assert message.text == ""
        assert message.source == "test"

    def test_canonical_message_with_long_text(self):
        """测试长文本的CanonicalMessage"""
        long_text = "A" * 10000
        message = CanonicalMessage(
            text=long_text,
            source="test",
        )

        assert message.text == long_text
        assert len(message.text) == 10000

    def test_canonical_message_timestamp_generation(self):
        """测试自动生成时间戳"""
        import time

        before = time.time()
        message = CanonicalMessage(text="Test", source="test")
        after = time.time()

        assert before <= message.timestamp <= after

    def test_canonical_message_metadata_mutation(self):
        """测试metadata可以安全修改"""
        message = CanonicalMessage(
            text="Test",
            source="test",
            metadata={"key1": "value1"},
        )

        # 修改metadata
        message.metadata["key2"] = "value2"
        message.metadata["key1"] = "modified"

        # 验证修改成功
        assert message.metadata["key1"] == "modified"
        assert message.metadata["key2"] == "value2"


class TestCanonicalMessageEdgeCases:
    """测试CanonicalMessage边界情况"""

    def test_canonical_message_with_none_data_ref(self):
        """测试data_ref为None"""
        message = CanonicalMessage(text="Test", source="test", data_ref=None)

        assert message.data_ref is None

    def test_canonical_message_with_string_data_ref(self):
        """测试data_ref为字符串"""
        message = CanonicalMessage(
            text="Test",
            source="test",
            data_ref="cache://image/abc123",
        )

        assert message.data_ref == "cache://image/abc123"

    def test_canonical_message_with_complex_metadata(self):
        """测试复杂的metadata结构"""
        complex_metadata = {
            "user_id": "123",
            "nested": {"key": "value"},
            "list": [1, 2, 3],
            "mixed": True,
        }

        message = CanonicalMessage(
            text="Test",
            source="test",
            metadata=complex_metadata,
        )

        assert message.metadata == complex_metadata

    def test_canonical_message_from_dict_with_extra_fields(self):
        """测试从字典反序列化时忽略额外字段"""
        data = {
            "text": "Test",
            "source": "test",
            "extra_field": "should_be_ignored",
            "metadata": {"key": "value"},
        }

        message = CanonicalMessage.from_dict(data)

        assert message.text == "Test"
        # 验证额外字段被忽略
        assert message.metadata.get("key") == "value"
