"""
测试 GuardNormalizer（pytest）

运行: uv run pytest tests/domains/normalization/test_guard_normalizer.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.domains.normalization.normalizers.guard_normalizer import GuardNormalizer
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage


# =============================================================================
# 初始化测试
# =============================================================================

def test_guard_normalizer_init():
    """测试 GuardNormalizer 初始化"""
    normalizer = GuardNormalizer()

    assert normalizer is not None
    assert normalizer.can_handle("guard") is True
    assert normalizer.can_handle("text") is False
    assert normalizer.can_handle("gift") is False
    assert normalizer.can_handle("superchat") is False
    assert normalizer.priority == 100


# =============================================================================
# Guard 数据解析测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_basic_guard():
    """测试基本大航海解析"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "level": "舰长"
        },
        source="bili_danmaku",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert isinstance(result, NormalizedMessage)
    assert result.data_type == "guard"
    assert result.source == "bili_danmaku"
    assert result.content.type == "text"
    assert "张三" in result.text
    assert "舰长" in result.text
    assert result.text == "张三 开通了舰长"


@pytest.mark.asyncio
async def test_guard_normalizer_dict_content():
    """测试字典格式的大航海数据"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "李四",
            "level": "总督"
        },
        source="bili_danmaku",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.type == "text"
    assert result.text == "李四 开通了总督"


# =============================================================================
# GuardContent 创建测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_content_display_text():
    """测试 GuardContent 的显示文本"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "王五",
            "level": "提督"
        },
        source="bili_danmaku",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.get_display_text() == "王五 开通了提督"
    assert result.text == "王五 开通了提督"


@pytest.mark.asyncio
async def test_guard_content_importance():
    """测试 GuardContent 的重要性"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # TextContent 的重要性应该是 0.3
    assert result.importance == 0.3
    assert result.content.get_importance() == 0.3


# =============================================================================
# 各种 Guard 等级测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_all_levels():
    """测试所有大航海等级"""
    normalizer = GuardNormalizer()

    test_cases = [
        ("用户A", "舰长", "用户A 开通了舰长"),
        ("用户B", "提督", "用户B 开通了提督"),
        ("用户C", "总督", "用户C 开通了总督"),
        ("用户D", "大航海", "用户D 开通了大航海"),
    ]

    for user, level, expected_text in test_cases:
        raw_data = RawData(
            content={
                "user": user,
                "level": level
            },
            source="test",
            data_type="guard",
            metadata={}
        )

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.text == expected_text
        assert user in result.text
        assert level in result.text


@pytest.mark.asyncio
async def test_guard_normalizer_custom_level():
    """测试自定义大航海等级"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "土豪",
            "level": "超级总督"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "土豪 开通了超级总督"


# =============================================================================
# 边界情况和错误处理测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_non_dict_content():
    """测试非字典内容"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content="Not a dict",
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    # 应该仍然返回结果，使用字符串表示
    assert result is not None
    assert "Not a dict" in result.text


@pytest.mark.asyncio
async def test_guard_normalizer_missing_fields():
    """测试缺少字段（使用默认值）"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={},  # 空字典
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "未知用户 开通了大航海"


@pytest.mark.asyncio
async def test_guard_normalizer_missing_user():
    """测试缺少用户名"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "level": "舰长"
            # 缺少 user
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "未知用户 开通了舰长"


@pytest.mark.asyncio
async def test_guard_normalizer_missing_level():
    """测试缺少等级"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "张三"
            # 缺少 level
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == "张三 开通了大航海"  # 默认等级


@pytest.mark.asyncio
async def test_guard_normalizer_empty_fields():
    """测试空字段"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "",
            "level": ""
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.text == " 开通了"  # 空字符串


@pytest.mark.asyncio
async def test_guard_normalizer_none_values():
    """测试 None 值"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": None,
            "level": None
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # None 会被转换为字符串 "None"
    assert "None" in result.text


# =============================================================================
# 特殊字符测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_unicode_username():
    """测试 Unicode 用户名"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "用户🎉❤️",
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "用户🎉❤️" in result.text
    assert "舰长" in result.text


@pytest.mark.asyncio
async def test_guard_normalizer_special_chars_in_level():
    """测试等级名称中的特殊字符"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "level": "超级总督★"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "超级总督★" in result.text


@pytest.mark.asyncio
async def test_guard_normalizer_long_username():
    """测试超长用户名"""
    normalizer = GuardNormalizer()

    long_username = "A" * 100

    raw_data = RawData(
        content={
            "user": long_username,
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert long_username in result.text
    assert len(result.text) > 100


# =============================================================================
# 元数据测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_metadata_preservation():
    """测试保留原始元数据"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "level": "舰长"
        },
        source="bili_danmaku",
        data_type="guard",
        metadata={
            "room_id": "12345",
            "gift_count": 100,
            "extra_info": "test"
        },
        timestamp=1234567890.0
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.metadata["room_id"] == "12345"
    assert result.metadata["gift_count"] == 100
    assert result.metadata["extra_info"] == "test"
    assert result.metadata["source"] == "bili_danmaku"
    assert result.metadata["original_timestamp"] == 1234567890.0
    assert result.timestamp == 1234567890.0


# =============================================================================
# 不同数据源测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_different_sources():
    """测试不同数据源"""
    normalizer = GuardNormalizer()

    sources = ["bili_danmaku", "douyu", "huya", "test_source"]

    for source in sources:
        raw_data = RawData(
            content={
                "user": "用户",
                "level": "舰长"
            },
            source=source,
            data_type="guard",
            metadata={}
        )

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.source == source
        assert result.metadata["source"] == source


# =============================================================================
# TextContent 相关测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_content_is_text_content():
    """测试 GuardContent 实际上是 TextContent"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # GuardNormalizer 使用 TextContent
    assert result.content.type == "text"
    assert hasattr(result.content, "text")
    assert result.content.text == result.text


@pytest.mark.asyncio
async def test_guard_content_special_handling():
    """测试 GuardContent 特殊处理判断"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # TextContent 的 requires_special_handling() 默认检查 importance > 0.8
    # Guard 使用 TextContent，importance = 0.3，所以不需要特殊处理
    assert result.content.requires_special_handling() is False


# =============================================================================
# 数字类型内容测试
# =============================================================================

@pytest.mark.asyncio
async def test_guard_normalizer_numeric_content():
    """测试数字类型内容"""
    normalizer = GuardNormalizer()

    raw_data = RawData(
        content=12345,
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "12345" in result.text


@pytest.mark.asyncio
async def test_guard_normalizer_mixed_types():
    """测试混合类型"""
    normalizer = GuardNormalizer()

    # user 是数字，level 是字符串
    raw_data = RawData(
        content={
            "user": 12345,
            "level": "舰长"
        },
        source="test",
        data_type="guard",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # 数字会被转换为字符串
    assert "12345" in result.text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
