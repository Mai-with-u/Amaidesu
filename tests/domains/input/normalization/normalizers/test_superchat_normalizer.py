"""
测试 SuperChatNormalizer（pytest）

运行: uv run pytest tests/domains/normalization/test_superchat_normalizer.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from src.domains.input.normalization.normalizers.superchat_normalizer import SuperChatNormalizer
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage


# =============================================================================
# 初始化测试
# =============================================================================


def test_superchat_normalizer_init():
    """测试 SuperChatNormalizer 初始化"""
    normalizer = SuperChatNormalizer()

    assert normalizer is not None
    assert normalizer.can_handle("superchat") is True
    assert normalizer.can_handle("text") is False
    assert normalizer.can_handle("gift") is False
    assert normalizer.priority == 100


# =============================================================================
# SuperChat 数据解析测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_normalizer_basic_superchat():
    """测试基本醒目留言解析"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "张三", "content": "感谢主播的精彩直播！", "price": 10.0},
        source="bili_danmaku",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert isinstance(result, NormalizedMessage)
    assert result.data_type == "superchat"
    assert result.source == "bili_danmaku"
    assert result.content.type == "super_chat"
    assert result.content.user == "张三"
    assert result.content.content == "感谢主播的精彩直播！"
    assert result.content.get_importance() == 0.1
    assert result.content.amount == 10.0


@pytest.mark.asyncio
async def test_superchat_normalizer_with_user_id():
    """测试带用户ID的醒目留言（SuperChatNormalizer不提取user_id，使用默认空值）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "李四", "user_id": "123456", "content": "继续加油！", "price": 50.0},
        source="bili_danmaku",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "李四"
    # SuperChatNormalizer 不提取 user_id 字段，使用默认空字符串
    assert result.content.user_id == ""
    # get_user_id() returns empty string (not None)
    assert result.content.get_user_id() in (None, "")


# =============================================================================
# SuperChatContent 创建测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_content_get_display_text():
    """测试 SuperChatContent 的显示文本"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "张三", "content": "支持主播！", "price": 30.0},
        source="bili_danmaku",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.get_display_text() == "醒目留言: 支持主播！"
    assert result.text == "醒目留言: 支持主播！"


@pytest.mark.asyncio
async def test_superchat_content_importance_calculation():
    """测试 SuperChatContent 重要性计算"""
    normalizer = SuperChatNormalizer()

    # 低金额醒目留言
    raw_data1 = RawData(
        content={"user": "用户A", "content": "测试", "price": 10.0}, source="test", data_type="superchat", metadata={}
    )

    result1 = await normalizer.normalize(raw_data1)
    # 10元 = 0.1 重要性
    assert result1.content.get_importance() == 0.1

    # 高金额醒目留言
    raw_data2 = RawData(
        content={"user": "用户B", "content": "测试", "price": 80.0}, source="test", data_type="superchat", metadata={}
    )

    result2 = await normalizer.normalize(raw_data2)
    # 80元 = 0.8 重要性
    assert result2.content.get_importance() == 0.8
    assert result2.content.get_importance() > result1.content.get_importance()


@pytest.mark.asyncio
async def test_superchat_content_requires_special_handling():
    """测试 SuperChatContent 特殊处理判断"""
    normalizer = SuperChatNormalizer()

    # 低金额（<= 50元，不需要特殊处理）
    raw_data1 = RawData(
        content={"user": "用户A", "content": "测试", "price": 30.0}, source="test", data_type="superchat", metadata={}
    )

    result1 = await normalizer.normalize(raw_data1)
    assert result1.content.requires_special_handling() is False

    # 高金额（> 50元，需要特殊处理）
    raw_data2 = RawData(
        content={"user": "用户B", "content": "测试", "price": 60.0}, source="test", data_type="superchat", metadata={}
    )

    result2 = await normalizer.normalize(raw_data2)
    assert result2.content.requires_special_handling() is True


# =============================================================================
# 各种价格层级测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_normalizer_price_levels():
    """测试不同价格层级的醒目留言"""
    normalizer = SuperChatNormalizer()

    test_cases = [
        (3.0, 0.03),  # 3元 = 0.03 重要性
        (10.0, 0.1),  # 10元 = 0.1 重要性
        (20.0, 0.2),  # 20元 = 0.2 重要性
        (50.0, 0.5),  # 50元 = 0.5 重要性
        (100.0, 1.0),  # 100元 = 1.0 重要性（上限）
        (200.0, 1.0),  # 200元 = 1.0 重要性（上限）
    ]

    for price, expected_importance in test_cases:
        raw_data = RawData(
            content={"user": "用户", "content": f"{price}元醒目留言", "price": price},
            source="test",
            data_type="superchat",
            metadata={},
        )

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.content.amount == price
        assert result.content.get_importance() == expected_importance


@pytest.mark.asyncio
async def test_superchat_normalizer_free_superchat():
    """测试免费醒目留言（0元）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "用户", "content": "免费醒目留言", "price": 0.0},
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.amount == 0.0
    assert result.content.get_importance() == 0.0


@pytest.mark.asyncio
async def test_superchat_normalizer_small_amount():
    """测试小额醒目留言（< 1元）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "用户", "content": "小额醒目留言", "price": 0.5},
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.amount == 0.5
    assert result.content.get_importance() == 0.005


# =============================================================================
# 边界情况和错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_normalizer_non_dict_content():
    """测试非字典内容（应该返回 None）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(content="Not a dict", source="test", data_type="superchat", metadata={})

    result = await normalizer.normalize(raw_data)

    assert result is None


@pytest.mark.asyncio
async def test_superchat_normalizer_missing_fields():
    """测试缺少字段（使用默认值）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={},  # 空字典
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "未知用户"
    assert result.content.content == ""
    assert result.content.amount == 0.0


@pytest.mark.asyncio
async def test_superchat_normalizer_partial_fields():
    """测试部分字段"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={
            "user": "张三",
            "content": "只有留言内容",
            # 缺少 price
        },
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.user == "张三"
    assert result.content.content == "只有留言内容"
    assert result.content.amount == 0.0  # 默认值


@pytest.mark.asyncio
async def test_superchat_normalizer_negative_price():
    """测试负价格（边界情况）"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "用户", "content": "测试", "price": -10.0}, source="test", data_type="superchat", metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.amount == -10.0
    # 负价格会产生负重要性（虽然不合理，但测试边界情况）
    assert result.content.get_importance() < 0.0


@pytest.mark.asyncio
async def test_superchat_normalizer_empty_content():
    """测试空留言内容"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "用户", "content": "", "price": 30.0}, source="test", data_type="superchat", metadata={}
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.content == ""
    assert result.content.get_display_text() == "醒目留言: "


@pytest.mark.asyncio
async def test_superchat_normalizer_very_long_content():
    """测试超长留言内容"""
    normalizer = SuperChatNormalizer()

    long_content = "这是一条非常长的醒目留言内容" * 100

    raw_data = RawData(
        content={"user": "用户", "content": long_content, "price": 50.0},
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert result.content.content == long_content
    assert len(result.content.content) > 1000


# =============================================================================
# 元数据测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_normalizer_metadata_preservation():
    """测试保留原始元数据"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "张三", "content": "感谢！", "price": 50.0},
        source="bili_danmaku",
        data_type="superchat",
        metadata={"room_id": "12345", "extra_info": "test"},
        timestamp=1234567890.0,
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
async def test_superchat_normalizer_different_sources():
    """测试不同数据源"""
    normalizer = SuperChatNormalizer()

    sources = ["bili_danmaku", "youtube", "test_source"]

    for source in sources:
        raw_data = RawData(
            content={"user": "用户", "content": f"来自{source}的醒目留言", "price": 30.0},
            source=source,
            data_type="superchat",
            metadata={},
        )

        result = await normalizer.normalize(raw_data)

        assert result is not None
        assert result.source == source
        assert result.metadata["source"] == source


# =============================================================================
# 特殊字符测试
# =============================================================================


@pytest.mark.asyncio
async def test_superchat_normalizer_unicode_content():
    """测试 Unicode 留言内容"""
    normalizer = SuperChatNormalizer()

    raw_data = RawData(
        content={"user": "用户", "content": "感谢支持！🎉❤️ 加油！💪", "price": 30.0},
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "🎉" in result.content.content
    assert "❤️" in result.content.content
    assert "💪" in result.content.content


@pytest.mark.asyncio
async def test_superchat_normalizer_multiline_content():
    """测试多行留言内容"""
    normalizer = SuperChatNormalizer()

    multiline_content = """第一行
第二行
第三行"""

    raw_data = RawData(
        content={"user": "用户", "content": multiline_content, "price": 30.0},
        source="test",
        data_type="superchat",
        metadata={},
    )

    result = await normalizer.normalize(raw_data)

    assert result is not None
    assert "第一行" in result.content.content
    assert "第二行" in result.content.content
    assert "第三行" in result.content.content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
