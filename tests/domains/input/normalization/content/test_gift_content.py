"""
测试 GiftContent (礼物内容类型)

运行: uv run pytest tests/domains/input/normalization/content/test_gift_content.py -v
"""

import pytest
from src.domains.input.normalization.content.gift_content import GiftContent


# =============================================================================
# 创建和基本功能测试
# =============================================================================


def test_gift_content_creation():
    """测试 GiftContent 创建"""
    content = GiftContent(
        user="张三",
        user_id="12345",
        gift_name="火箭",
        gift_level=10,
        count=1,
        value=100.0,
    )
    assert content.type == "gift"
    assert content.user == "张三"
    assert content.gift_name == "火箭"
    assert content.gift_level == 10
    assert content.count == 1
    assert content.value == 100.0


def test_gift_content_default_values():
    """测试默认值"""
    content = GiftContent()
    assert content.user == ""
    assert content.user_id == ""
    assert content.gift_name == ""
    assert content.gift_level == 1
    assert content.count == 1
    assert content.value == 0.0
    # 注意：importance 会由验证器自动计算
    # gift_level=1, value=0.0, count=1
    # 计算: min(1/10, 1.0) + min(0/10000, 0.3) + min(1/10, 0.2)
    #     = 0.1 + 0.0 + 0.1 = 0.2
    assert content.importance == 0.2


# =============================================================================
# importance 自动计算测试
# =============================================================================


def test_gift_content_importance_calculation():
    """测试重要性自动计算"""
    # 等级10，价值10000元，数量10
    content = GiftContent(gift_level=10, value=10000, count=10)

    # 基础: min(10/10, 1.0) = 1.0
    # 价值: min(10000/10000, 0.3) = 0.3
    # 数量: min(10/10, 0.2) = 0.2
    # 总计: min(1.0 + 0.3 + 0.2, 1.0) = 1.0
    assert content.importance == 1.0


def test_gift_content_importance_low_level():
    """测试低等级礼物"""
    # 等级1，价值1元，数量1
    content = GiftContent(gift_level=1, value=1, count=1)

    # 基础: 0.1
    # 价值: 0.0001
    # 数量: 0.1
    expected = 0.1 + 0.0001 + 0.1
    assert abs(content.importance - expected) < 0.01


def test_gift_content_importance_value_cap():
    """测试价值加成上限"""
    # 高价值但等级低
    content = GiftContent(gift_level=1, value=100000, count=1)

    # 价值加成最多0.3
    assert content.importance <= 1.0


def test_gift_content_importance_count_cap():
    """测试数量加成上限"""
    # 大数量但等级低
    content = GiftContent(gift_level=1, value=1, count=1000)

    # 数量加成最多0.2
    assert content.importance <= 1.0


# =============================================================================
# get_importance() 测试
# =============================================================================


def test_gift_content_get_importance():
    """测试获取重要性"""
    content = GiftContent(gift_level=5, value=50, count=5)
    importance = content.get_importance()
    assert importance == content.importance


# =============================================================================
# get_display_text() 测试
# =============================================================================


def test_gift_content_get_display_text():
    """测试获取显示文本"""
    content = GiftContent(user="张三", gift_name="火箭", count=1)
    display = content.get_display_text()
    assert display == "张三 送出了 1 个 火箭"


def test_gift_content_get_display_text_multiple():
    """测试多个礼物的显示"""
    content = GiftContent(user="李四", gift_name="小花", count=10)
    display = content.get_display_text()
    assert display == "李四 送出了 10 个 小花"


def test_gift_content_get_display_text_empty_user():
    """测试空用户名"""
    content = GiftContent(user="", gift_name="礼物", count=1)
    display = content.get_display_text()
    assert display == " 送出了 1 个 礼物"


# =============================================================================
# get_user_id() 测试
# =============================================================================


def test_gift_content_get_user_id():
    """测试获取用户ID"""
    content = GiftContent(user_id="12345")
    assert content.get_user_id() == "12345"


def test_gift_content_get_user_id_empty():
    """测试空用户ID"""
    content = GiftContent()
    assert content.get_user_id() == ""


# =============================================================================
# requires_special_handling() 测试
# =============================================================================


def test_gift_content_requires_special_handling_true():
    """测试高价值礼物需要特殊处理"""
    # importance > 0.7
    content = GiftContent(gift_level=8, value=100, count=5)
    if content.importance > 0.7:
        assert content.requires_special_handling() is True


def test_gift_content_requires_special_handling_false():
    """测试低价值礼物不需要特殊处理"""
    # 低等级低价值
    content = GiftContent(gift_level=1, value=1, count=1)
    if content.importance <= 0.7:
        assert content.requires_special_handling() is False


def test_gift_content_requires_special_handling_threshold():
    """测试 0.7 阈值边界"""
    # 创建 importance 接近 0.7 的礼物
    content = GiftContent(gift_level=7, value=10, count=1)
    # 根据计算结果验证
    assert content.requires_special_handling() == (content.importance > 0.7)


# =============================================================================
# Pydantic 验证测试
# =============================================================================


def test_gift_content_type_literal():
    """测试 type 字段为字面量"""
    content = GiftContent(gift_name="火箭")
    assert content.type == "gift"


def test_gift_content_serialization():
    """测试序列化"""
    content = GiftContent(user="张三", gift_name="火箭", gift_level=10, count=1, value=100)
    data = content.model_dump()

    assert data["user"] == "张三"
    assert data["gift_name"] == "火箭"
    assert data["gift_level"] == 10
    assert data["count"] == 1
    assert data["value"] == 100


def test_gift_content_deserialization():
    """测试反序列化"""
    data = {
        "type": "gift",
        "user": "张三",
        "gift_name": "火箭",
        "gift_level": 10,
        "count": 1,
        "value": 100.0,
    }
    content = GiftContent(**data)

    assert content.user == "张三"
    assert content.gift_name == "火箭"
    assert content.importance > 0  # 应该被自动计算


# =============================================================================
# 继承和多态测试
# =============================================================================


def test_gift_content_is_structured_content():
    """测试 GiftContent 是 StructuredContent 的子类"""
    from src.domains.input.normalization.content.base import StructuredContent

    content = GiftContent(gift_name="火箭")
    assert isinstance(content, StructuredContent)


def test_gift_content_polymorphism():
    """测试多态调用"""
    from src.domains.input.normalization.content.base import StructuredContent

    content: StructuredContent = GiftContent(user="张三", gift_name="火箭", gift_level=10)

    # 通过基类类型调用子类方法
    assert 0 <= content.get_importance() <= 1
    assert "张三" in content.get_display_text()
    assert content.get_user_id() == ""


# =============================================================================
# 边界条件测试
# =============================================================================


def test_gift_content_zero_count():
    """测试数量为0"""
    content = GiftContent(gift_name="火箭", count=0)
    assert content.count == 0


def test_gift_content_negative_value():
    """测试负价值"""
    content = GiftContent(gift_name="火箭", value=-100)
    # 负价值应该被接受（实际可能不应该，但这里测试Pydantic行为）
    assert content.value == -100


def test_gift_content_very_high_level():
    """测试超高等级"""
    content = GiftContent(gift_name="超级礼物", gift_level=1000, value=0, count=1)
    # 等级加成应该被限制在 1.0 以内
    assert content.importance <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
