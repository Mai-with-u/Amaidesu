"""
测试 GiftNormalizer（pytest）

运行: uv run pytest tests/domains/normalization/test_gift_normalizer.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.domains.normalization.normalizers.gift_normalizer import GiftNormalizer
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage


# =============================================================================
# 初始化测试
# =============================================================================

def test_gift_normalizer_init():
    """测试 GiftNormalizer 初始化"""
    normalizer = GiftNormalizer()

    assert normalizer is not None
    assert normalizer.can_handle("gift") is True
    assert normalizer.can_handle("text") is False
    assert normalizer.can_handle("superchat") is False
    assert normalizer.priority == 100


# =============================================================================
# 礼物数据解析测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_normalizer_basic_gift():
    """测试基本礼物解析"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "gift_name": "小星星",
            "gift_level": 1,
            "count": 1,
            "value": 1.0
        },
        source="bili_danmaku",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert isinstance(result, NormalizedMessage)
    assert result.data_type == "gift"
    assert result.source == "bili_danmaku"
    assert result.content.type == "gift"
    assert result.content.user == "张三"
    assert result.content.gift_name == "小星星"
    assert result.content.gift_level == 1
    assert result.content.count == 1
    assert result.content.value == 1.0


@pytest.mark.asyncio
async def test_gift_normalizer_multiple_gifts():
    """测试多个礼物（count > 1）"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "李四",
            "gift_name": "花花",
            "gift_level": 2,
            "count": 10,
            "value": 50.0
        },
        source="bili_danmaku",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "李四"
    assert result.content.gift_name == "花花"
    assert result.content.count == 10
    assert result.content.value == 50.0


@pytest.mark.asyncio
async def test_gift_normalizer_high_value_gift():
    """测试高价值礼物"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "王五",
            "gift_name": "超级火箭",
            "gift_level": 10,
            "count": 1,
            "value": 2000.0
        },
        source="bili_danmaku",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.gift_level == 10
    assert result.content.value == 2000.0
    # 高价值礼物应该有较高的重要性
    assert result.importance > 0.7


# =============================================================================
# GiftContent 创建测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_content_get_display_text():
    """测试 GiftContent 的显示文本"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "gift_name": "小星星",
            "gift_level": 1,
            "count": 5,
            "value": 5.0
        },
        source="bili_danmaku",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.get_display_text() == "张三 送出了 5 个 小星星"
    assert result.text == "张三 送出了 5 个 小星星"


@pytest.mark.asyncio
async def test_gift_content_importance_calculation():
    """测试 GiftContent 重要性计算"""
    normalizer = GiftNormalizer()

    # 低价值礼物
    raw_data1 = RawData(
        content={
            "user": "用户A",
            "gift_name": "低级礼物",
            "gift_level": 1,
            "count": 1,
            "value": 1.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result1 = await normalizer.normalize(raw_data1)
    assert result1.content.importance < 0.5

    # 高价值礼物
    raw_data2 = RawData(
        content={
            "user": "用户B",
            "gift_name": "高级礼物",
            "gift_level": 10,
            "count": 10,
            "value": 5000.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result2 = await normalizer.normalize(raw_data2)
    assert result2.content.importance > result1.content.importance


@pytest.mark.asyncio
async def test_gift_content_requires_special_handling():
    """测试 GiftContent 特殊处理判断"""
    normalizer = GiftNormalizer()

    # 低价值礼物（不需要特殊处理）
    raw_data1 = RawData(
        content={
            "user": "用户A",
            "gift_name": "低级礼物",
            "gift_level": 1,
            "count": 1,
            "value": 1.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result1 = await normalizer.normalize(raw_data1)
    assert result1.content.requires_special_handling() is False

    # 高价值礼物（需要特殊处理）
    raw_data2 = RawData(
        content={
            "user": "用户B",
            "gift_name": "高级礼物",
            "gift_level": 10,
            "count": 10,
            "value": 10000.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result2 = await normalizer.normalize(raw_data2)
    assert result2.content.requires_special_handling() is True


# =============================================================================
# 各种礼物类型测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_normalizer_level_1_gift():
    """测试等级1礼物"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "gift_name": "爱心",
            "gift_level": 1,
            "count": 1,
            "value": 0.5
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.gift_level == 1
    # 基础重要性应该是 0.1 (1/10)
    assert 0.0 < result.content.importance < 0.3


@pytest.mark.asyncio
async def test_gift_normalizer_level_5_gift():
    """测试等级5礼物"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "gift_name": "中级礼物",
            "gift_level": 5,
            "count": 1,
            "value": 50.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.gift_level == 5
    # 基础重要性应该是 0.5 (5/10)
    assert 0.4 < result.content.importance < 0.7


@pytest.mark.asyncio
async def test_gift_normalizer_level_10_gift():
    """测试等级10礼物"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "gift_name": "顶级礼物",
            "gift_level": 10,
            "count": 1,
            "value": 500.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.gift_level == 10
    # 基础重要性应该是 1.0 (max)
    assert result.content.importance > 0.8


@pytest.mark.asyncio
async def test_gift_normalizer_with_user_id():
    """测试带用户ID的礼物（GiftNormalizer不提取user_id，使用默认空值）"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "user_id": "123456",
            "gift_name": "礼物",
            "gift_level": 1,
            "count": 1,
            "value": 1.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    # GiftNormalizer 不提取 user_id 字段，使用默认空字符串
    assert result.content.user_id == ""
    # get_user_id() returns None for empty string
    assert result.content.get_user_id() in (None, "")


# =============================================================================
# 边界情况和错误处理测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_normalizer_non_dict_content():
    """测试非字典内容（应该返回 None）"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content="Not a dict",
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is None


@pytest.mark.asyncio
async def test_gift_normalizer_missing_fields():
    """测试缺少字段（使用默认值）"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={},  # 空字典
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "未知用户"
    assert result.content.gift_name == "未知礼物"
    assert result.content.gift_level == 1
    assert result.content.count == 1
    assert result.content.value == 0.0


@pytest.mark.asyncio
async def test_gift_normalizer_partial_fields():
    """测试部分字段"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "gift_name": "礼物"
            # 缺少其他字段
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "张三"
    assert result.content.gift_name == "礼物"
    assert result.content.gift_level == 1  # 默认值
    assert result.content.count == 1  # 默认值
    assert result.content.value == 0.0  # 默认值


@pytest.mark.asyncio
async def test_gift_normalizer_zero_count():
    """测试数量为0"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "用户",
            "gift_name": "礼物",
            "gift_level": 1,
            "count": 0,
            "value": 0.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.count == 0
    assert result.content.get_display_text() == "用户 送出了 0 个 礼物"


@pytest.mark.asyncio
async def test_gift_normalizer_very_large_count():
    """测试超大数量"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "土豪",
            "gift_name": "小星星",
            "gift_level": 1,
            "count": 9999,
            "value": 9999.0
        },
        source="test",
        data_type="gift",
        metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.count == 9999
    # 数量加成应该达到上限 (0.2)
    assert result.content.importance > 0.2


# =============================================================================
# 元数据测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_normalizer_metadata_preservation():
    """测试保留原始元数据"""
    normalizer = GiftNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "gift_name": "礼物",
            "gift_level": 1,
            "count": 1,
            "value": 1.0
        },
        source="bili_danmaku",
        data_type="gift",
        metadata={
            "room_id": "12345",
            "extra_info": "test"
        },
        timestamp=1234567890.0
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.metadata["room_id"] == "12345"
    assert result.metadata["extra_info"] == "test"
    assert result.metadata["source"] == "bili_danmaku"
    assert result.metadata["original_timestamp"] == 1234567890.0
    assert result.timestamp == 1234567890.0


# =============================================================================
# 不同数据源测试
# =============================================================================

@pytest.mark.asyncio
async def test_gift_normalizer_different_sources():
    """测试不同数据源"""
    normalizer = GiftNormalizer()

    sources = ["bili_danmaku", "douyu", "huya", "test"]

    for source in sources:
        raw_data = RawData(
            content={
                "user": "用户",
                "gift_name": "礼物",
                "gift_level": 1,
                "count": 1,
                "value": 1.0
            },
            source=source,
            data_type="gift",
            metadata={}
        )

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.source == source
        assert result.metadata["source"] == source


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
