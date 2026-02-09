"""
测试 TextContent (文本内容类型)

运行: uv run pytest tests/domains/input/normalization/content/test_text_content.py -v
"""

import pytest
from src.domains.input.normalization.content.text_content import TextContent


# =============================================================================
# 创建和基本功能测试
# =============================================================================


def test_text_content_creation():
    """测试 TextContent 创建"""
    content = TextContent(text="测试消息")
    assert content.type == "text"
    assert content.text == "测试消息"
    assert content.user is None
    assert content.user_id is None


def test_text_content_with_user():
    """测试带用户信息的 TextContent"""
    content = TextContent(text="测试消息", user="测试用户", user_id="12345")
    assert content.text == "测试消息"
    assert content.user == "测试用户"
    assert content.user_id == "12345"


def test_text_content_default_values():
    """测试默认值"""
    content = TextContent()
    assert content.text == ""
    assert content.user is None
    assert content.user_id is None


# =============================================================================
# get_importance() 测试
# =============================================================================


def test_text_content_get_importance():
    """测试获取重要性"""
    content = TextContent(text="任意消息")
    assert content.get_importance() == 0.3


# =============================================================================
# get_display_text() 测试
# =============================================================================


def test_text_content_get_display_text():
    """测试获取显示文本"""
    content = TextContent(text="这是测试消息")
    assert content.get_display_text() == "这是测试消息"


def test_text_content_get_display_text_empty():
    """测试空文本的显示"""
    content = TextContent(text="")
    assert content.get_display_text() == ""


def test_text_content_get_display_text_special_chars():
    """测试特殊字符显示"""
    text = "测试!!!消息@@@\n换行"
    content = TextContent(text=text)
    assert content.get_display_text() == text


# =============================================================================
# get_user_id() 测试
# =============================================================================


def test_text_content_get_user_id():
    """测试获取用户ID"""
    content = TextContent(text="测试", user_id="12345")
    assert content.get_user_id() == "12345"


def test_text_content_get_user_id_none():
    """测试无用户ID时返回 None"""
    content = TextContent(text="测试")
    assert content.get_user_id() is None


# =============================================================================
# requires_special_handling() 测试
# =============================================================================


def test_text_content_requires_special_handling():
    """测试是否需要特殊处理"""
    content = TextContent(text="测试消息")
    # importance = 0.3, 不大于 0.8
    assert content.requires_special_handling() is False


# =============================================================================
# Pydantic 验证测试
# =============================================================================


def test_text_content_type_literal():
    """测试 type 字段为字面量"""
    content = TextContent(text="测试")
    assert content.type == "text"

    # 尝试修改 type 应该抛出 ValidationError
    # 因为 type 字段是 Literal["text"]，只能是 "text"
    data = content.model_dump()
    data["type"] = "invalid"
    # 重新创建时应该抛出验证错误
    with pytest.raises(Exception):  # ValidationError 是 Pydantic 的异常
        TextContent(**data)


def test_text_content_serialization():
    """测试序列化"""
    content = TextContent(text="测试消息", user="用户", user_id="123")
    data = content.model_dump()

    assert data["text"] == "测试消息"
    assert data["user"] == "用户"
    assert data["user_id"] == "123"
    assert data["type"] == "text"


def test_text_content_deserialization():
    """测试反序列化"""
    data = {
        "type": "text",
        "text": "测试消息",
        "user": "用户",
        "user_id": "123",
    }
    content = TextContent(**data)

    assert content.text == "测试消息"
    assert content.user == "用户"
    assert content.user_id == "123"


def test_text_content_json_serialization():
    """测试 JSON 序列化"""
    content = TextContent(text="测试消息", user="用户")
    json_str = content.model_dump_json()

    assert "测试消息" in json_str
    assert "用户" in json_str


# =============================================================================
# 继承和多态测试
# =============================================================================


def test_text_content_is_structured_content():
    """测试 TextContent 是 StructuredContent 的子类"""
    from src.domains.input.normalization.content.base import StructuredContent

    content = TextContent(text="测试")
    assert isinstance(content, StructuredContent)


def test_text_content_polymorphism():
    """测试多态调用"""
    from src.domains.input.normalization.content.base import StructuredContent

    content: StructuredContent = TextContent(text="测试消息", user_id="123")

    # 通过基类类型调用子类方法
    assert content.get_importance() == 0.3
    assert content.get_display_text() == "测试消息"
    assert content.get_user_id() == "123"


# =============================================================================
# 边界条件测试
# =============================================================================


def test_text_content_very_long():
    """测试超长文本"""
    long_text = "a" * 100000
    content = TextContent(text=long_text)
    assert content.text == long_text
    assert len(content.text) == 100000


def test_text_content_unicode():
    """测试 Unicode 字符"""
    text = "测试中文🎉emoji😊"
    content = TextContent(text=text)
    assert content.text == text
    assert content.get_display_text() == text


def test_text_content_newlines_and_tabs():
    """测试换行和制表符"""
    text = "第一行\n第二行\t制表符"
    content = TextContent(text=text)
    assert content.text == text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
