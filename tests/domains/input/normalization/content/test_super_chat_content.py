"""
测试 SuperChatContent (醒目留言内容类型)

运行: uv run pytest tests/domains/input/normalization/content/test_super_chat_content.py -v
"""

import pytest
from src.domains.input.normalization.content.super_chat_content import SuperChatContent


# =============================================================================
# 创建和基本功能测试
# =============================================================================


def test_super_chat_content_creation():
    """测试 SuperChatContent 创建"""
    content = SuperChatContent(
        user="张三",
        user_id="12345",
        amount=30.0,
        content="谢谢主播的精彩直播！",
    )
    assert content.type == "super_chat"
    assert content.user == "张三"
    assert content.user_id == "12345"
    assert content.amount == 30.0
    assert content.content == "谢谢主播的精彩直播！"


def test_super_chat_content_default_values():
    """测试默认值"""
    content = SuperChatContent()
    assert content.user == ""
    assert content.user_id == ""
    assert content.amount == 0.0
    assert content.content == ""


# =============================================================================
# get_importance() 测试
# =============================================================================


def test_super_chat_get_importance_low():
    """测试低金额醒目留言"""
    content = SuperChatContent(amount=10.0)
    # 10元 / 100 = 0.1
    assert content.get_importance() == 0.1


def test_super_chat_get_importance_medium():
    """测试中等金额醒目留言"""
    content = SuperChatContent(amount=50.0)
    # 50元 / 100 = 0.5
    assert content.get_importance() == 0.5


def test_super_chat_get_importance_high():
    """测试高金额醒目留言"""
    content = SuperChatContent(amount=100.0)
    # 100元 / 100 = 1.0
    assert content.get_importance() == 1.0


def test_super_chat_get_importance_cap():
    """测试重要性上限"""
    # 超过100元
    content = SuperChatContent(amount=500.0)
    # 应该限制在 1.0
    assert content.get_importance() == 1.0


def test_super_chat_get_importance_zero():
    """测试零金额"""
    content = SuperChatContent(amount=0.0)
    assert content.get_importance() == 0.0


# =============================================================================
# get_display_text() 测试
# =============================================================================


def test_super_chat_get_display_text():
    """测试获取显示文本"""
    content = SuperChatContent(content="谢谢大家的支持！")
    display = content.get_display_text()
    assert display == "醒目留言: 谢谢大家的支持！"


def test_super_chat_get_display_text_empty():
    """测试空内容"""
    content = SuperChatContent(content="")
    display = content.get_display_text()
    assert display == "醒目留言: "


def test_super_chat_get_display_text_long():
    """测试长内容"""
    long_text = "这是一条很长的醒目留言内容" * 10
    content = SuperChatContent(content=long_text)
    display = content.get_display_text()
    assert display.startswith("醒目留言: ")


def test_super_chat_get_display_text_special_chars():
    """测试特殊字符"""
    text = "测试!!!消息@@@emoji😊"
    content = SuperChatContent(content=text)
    display = content.get_display_text()
    assert display == f"醒目留言: {text}"


# =============================================================================
# get_user_id() 测试
# =============================================================================


def test_super_chat_get_user_id():
    """测试获取用户ID"""
    content = SuperChatContent(user_id="12345")
    assert content.get_user_id() == "12345"


def test_super_chat_get_user_id_none():
    """测试空用户ID"""
    content = SuperChatContent()
    assert content.get_user_id() == ""


# =============================================================================
# requires_special_handling() 测试
# =============================================================================


def test_super_chat_requires_special_handling_true():
    """测试高金额需要特殊处理（>50元）"""
    content = SuperChatContent(amount=100.0)
    assert content.requires_special_handling() is True


def test_super_chat_requires_special_handling_threshold():
    """测试50元阈值"""
    content = SuperChatContent(amount=50.0)
    # 50元不大于50，不需要特殊处理
    assert content.requires_special_handling() is False


def test_super_chat_requires_special_handling_false():
    """测试低金额不需要特殊处理"""
    content = SuperChatContent(amount=10.0)
    assert content.requires_special_handling() is False


def test_super_chat_requires_special_handling_boundary():
    """测试边界值（50.01元）"""
    content = SuperChatContent(amount=50.01)
    assert content.requires_special_handling() is True


# =============================================================================
# Pydantic 验证测试
# =============================================================================


def test_super_chat_type_literal():
    """测试 type 字段为字面量"""
    content = SuperChatContent(amount=10.0)
    assert content.type == "super_chat"


def test_super_chat_serialization():
    """测试序列化"""
    content = SuperChatContent(user="张三", amount=30.0, content="谢谢主播")
    data = content.model_dump()

    assert data["user"] == "张三"
    assert data["amount"] == 30.0
    assert data["content"] == "谢谢主播"


def test_super_chat_deserialization():
    """测试反序列化"""
    data = {
        "type": "super_chat",
        "user": "张三",
        "user_id": "12345",
        "amount": 30.0,
        "content": "谢谢主播",
    }
    content = SuperChatContent(**data)

    assert content.user == "张三"
    assert content.amount == 30.0
    assert content.content == "谢谢主播"


def test_super_chat_json_serialization():
    """测试 JSON 序列化"""
    content = SuperChatContent(user="张三", amount=30.0, content="谢谢主播")
    json_str = content.model_dump_json()

    assert "张三" in json_str
    assert "30" in json_str
    assert "谢谢主播" in json_str


# =============================================================================
# 继承和多态测试
# =============================================================================


def test_super_chat_is_structured_content():
    """测试 SuperChatContent 是 StructuredContent 的子类"""
    from src.domains.input.normalization.content.base import StructuredContent

    content = SuperChatContent(amount=10.0)
    assert isinstance(content, StructuredContent)


def test_super_chat_polymorphism():
    """测试多态调用"""
    from src.domains.input.normalization.content.base import StructuredContent

    content: StructuredContent = SuperChatContent(user="张三", amount=50.0, content="谢谢")

    # 通过基类类型调用子类方法
    assert content.get_importance() == 0.5
    assert "谢谢" in content.get_display_text()
    assert content.get_user_id() == ""


# =============================================================================
# 边界条件测试
# =============================================================================


def test_super_chat_negative_amount():
    """测试负金额"""
    content = SuperChatContent(amount=-10.0)
    # 负金额会被接受，get_importance() 返回 min(-10/100, 1.0) = -0.1
    assert content.amount == -10.0
    assert content.get_importance() == -0.1


def test_super_chat_very_high_amount():
    """测试超高金额"""
    content = SuperChatContent(amount=10000.0)
    # importance 应该限制在 1.0
    assert content.get_importance() == 1.0


def test_super_chat_empty_user():
    """测试空用户名"""
    content = SuperChatContent(user="", amount=10.0, content="测试")
    assert content.user == ""
    assert content.get_user_id() == ""


def test_super_chat_unicode_content():
    """测试 Unicode 内容"""
    content = SuperChatContent(content="测试中文🎉emoji😊")
    assert content.content == "测试中文🎉emoji😊"
    assert "测试中文" in content.get_display_text()


# =============================================================================
# 实际场景测试
# =============================================================================


def test_super_chat_real_world_example_1():
    """测试实际场景1：小额醒目留言"""
    content = SuperChatContent(
        user="粉丝A",
        user_id="001",
        amount=5.0,
        content="主播加油！",
    )

    assert content.get_importance() == 0.05
    assert content.requires_special_handling() is False
    assert "粉丝A" not in content.get_display_text()  # display_text 不包含用户名


def test_super_chat_real_world_example_2():
    """测试实际场景2：大额醒目留言"""
    content = SuperChatContent(
        user="土豪B",
        user_id="002",
        amount=200.0,
        content="主播太棒了！感谢你的精彩直播！",
    )

    assert content.get_importance() == 1.0
    assert content.requires_special_handling() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
