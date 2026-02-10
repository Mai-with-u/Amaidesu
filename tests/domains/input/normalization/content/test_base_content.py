"""
测试 StructuredContent 基类

运行: uv run pytest tests/domains/input/normalization/content/test_base_content.py -v
"""

import pytest

from src.domains.input.normalization.content.base import StructuredContent

# =============================================================================
# 抽象方法测试
# =============================================================================


def test_base_content_not_implemented_get_importance():
    """测试基类 get_importance 抛出 NotImplementedError"""
    content = StructuredContent(type="test")
    with pytest.raises(NotImplementedError):
        content.get_importance()


def test_base_content_not_implemented_get_display_text():
    """测试基类 get_display_text 抛出 NotImplementedError"""
    content = StructuredContent(type="test")
    with pytest.raises(NotImplementedError):
        content.get_display_text()


# =============================================================================
# 默认方法测试
# =============================================================================


def test_base_content_get_user_id_default():
    """测试 get_user_id 默认返回 None"""
    content = StructuredContent(type="test")
    assert content.get_user_id() is None


def test_base_content_requires_special_handling_default():
    """测试 requires_special_handling 默认行为"""
    # 由于 get_importance 未实现，此测试需要使用子类
    # 这里仅测试方法存在
    content = StructuredContent(type="test")
    assert hasattr(content, "requires_special_handling")


def test_base_content_repr():
    """测试字符串表示"""
    # 注意：__repr__ 方法会调用 get_importance()，
    # 而基类的 get_importance() 会抛出 NotImplementedError
    # 因此测试需要使用实现了 get_importance() 的子类
    content = TestContent(type="test")
    repr_str = repr(content)
    assert "TestContent" in repr_str
    assert "type=test" in repr_str
    assert "importance=0.5" in repr_str


# =============================================================================
# 子类实现测试
# =============================================================================


class TestContent(StructuredContent):
    """测试用子类"""

    def get_importance(self) -> float:
        return 0.5

    def get_display_text(self) -> str:
        return "测试内容"


def test_subclass_implementation():
    """测试子类正确实现"""
    content = TestContent(type="test_content")

    # 应该能正常调用
    assert content.get_importance() == 0.5
    assert content.get_display_text() == "测试内容"
    assert content.get_user_id() is None


def test_subclass_requires_special_handling():
    """测试子类 requires_special_handling"""
    content = TestContent(type="test_content")

    # importance = 0.5, 不大于 0.8
    assert content.requires_special_handling() is False


def test_subclass_requires_special_handling_high_importance():
    """测试高重要性的 requires_special_handling"""

    class HighImportanceContent(TestContent):
        def get_importance(self) -> float:
            return 0.9

    content = HighImportanceContent(type="high")

    # importance = 0.9, 大于 0.8
    assert content.requires_special_handling() is True


# =============================================================================
# Pydantic BaseModel 功能测试
# =============================================================================


def test_pydantic_validation():
    """测试 Pydantic 验证"""
    # type 字段是必需的
    with pytest.raises(Exception):
        TestContent()  # 缺少 type


def test_pydantic_serialization():
    """测试序列化"""
    content = TestContent(type="test_content")
    data = content.model_dump()

    assert data["type"] == "test_content"


def test_pydantic_deserialization():
    """测试反序列化"""
    data = {"type": "test_content"}
    content = TestContent(**data)

    assert content.type == "test_content"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
