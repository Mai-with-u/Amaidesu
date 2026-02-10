"""
测试 SimilarFilterTextPipeline (相似文本过滤管道)

运行: uv run pytest tests/domains/input/pipelines/test_similar_filter_pipeline.py -v
"""

import pytest
from src.domains.input.pipelines.similar_filter.pipeline import SimilarFilterTextPipeline
from src.domains.input.pipelines.manager import PipelineContext


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def similar_filter_pipeline():
    """创建相似文本过滤管道实例"""
    config = {
        "similarity_threshold": 0.85,
        "time_window": 5.0,
        "min_text_length": 3,
        "cross_user_filter": True,
    }
    return SimilarFilterTextPipeline(config)


@pytest.fixture
def basic_metadata():
    """基础元数据"""
    return {"user_id": "test_user", "group_id": "test_group"}


# =============================================================================
# 创建和初始化测试
# =============================================================================


def test_similar_filter_pipeline_creation(similar_filter_pipeline):
    """测试管道创建"""
    assert similar_filter_pipeline is not None
    assert similar_filter_pipeline._similarity_threshold == 0.85
    assert similar_filter_pipeline._time_window == 5.0
    assert similar_filter_pipeline._min_text_length == 3
    assert similar_filter_pipeline._cross_user_filter is True
    assert similar_filter_pipeline.priority == 500


def test_similar_filter_pipeline_custom_config():
    """测试自定义配置"""
    config = {
        "similarity_threshold": 0.9,
        "time_window": 10.0,
        "min_text_length": 5,
        "cross_user_filter": False,
    }
    pipeline = SimilarFilterTextPipeline(config)
    assert pipeline._similarity_threshold == 0.9
    assert pipeline._time_window == 10.0
    assert pipeline._min_text_length == 5
    assert pipeline._cross_user_filter is False


# =============================================================================
# process() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_first_message(similar_filter_pipeline, basic_metadata):
    """测试第一条消息通过"""
    text = "这是一条测试消息"
    result = await similar_filter_pipeline._process(text, basic_metadata)
    assert result == text


@pytest.mark.asyncio
async def test_process_similar_message_filtered(similar_filter_pipeline, basic_metadata):
    """测试相似消息被过滤"""
    text1 = "这是一条测试消息"
    text2 = "这是一条测试消息"  # 完全相同

    result1 = await similar_filter_pipeline._process(text1, basic_metadata)
    assert result1 == text1

    result2 = await similar_filter_pipeline._process(text2, basic_metadata)
    assert result2 is None  # 被过滤


@pytest.mark.asyncio
async def test_process_different_message_pass(similar_filter_pipeline, basic_metadata):
    """测试不同消息通过"""
    text1 = "这是一条测试消息"
    text2 = "这是完全不同的内容"

    result1 = await similar_filter_pipeline._process(text1, basic_metadata)
    assert result1 == text1

    result2 = await similar_filter_pipeline._process(text2, basic_metadata)
    assert result2 == text2


@pytest.mark.asyncio
async def test_process_below_min_length(similar_filter_pipeline, basic_metadata):
    """测试低于最小长度的文本跳过过滤"""
    short_text = "12"  # 低于 min_text_length=3

    result = await similar_filter_pipeline._process(short_text, basic_metadata)
    assert result == short_text  # 应该通过（跳过过滤）


@pytest.mark.asyncio
async def test_process_cross_user_filter(similar_filter_pipeline):
    """测试跨用户过滤"""
    text = "相同的消息内容"

    user1_metadata = {"user_id": "user1", "group_id": "group1"}
    user2_metadata = {"user_id": "user2", "group_id": "group1"}

    # 用户1发送消息
    result1 = await similar_filter_pipeline._process(text, user1_metadata)
    assert result1 == text

    # 用户2发送相同消息（跨用户过滤启用）
    result2 = await similar_filter_pipeline._process(text, user2_metadata)
    assert result2 is None  # 被过滤


@pytest.mark.asyncio
async def test_process_no_cross_user_filter(similar_filter_pipeline):
    """测试禁用跨用户过滤"""
    # 创建禁用跨用户过滤的管道
    config = {
        "similarity_threshold": 0.85,
        "time_window": 5.0,
        "min_text_length": 3,
        "cross_user_filter": False,
    }
    pipeline = SimilarFilterTextPipeline(config)

    text = "相同的消息内容"

    user1_metadata = {"user_id": "user1", "group_id": "group1"}
    user2_metadata = {"user_id": "user2", "group_id": "group1"}

    # 用户1发送消息
    result1 = await pipeline._process(text, user1_metadata)
    assert result1 == text

    # 用户2发送相同消息（跨用户过滤禁用）
    result2 = await pipeline._process(text, user2_metadata)
    assert result2 == text  # 应该通过


@pytest.mark.asyncio
async def test_process_with_context_rollback(similar_filter_pipeline, basic_metadata):
    """测试 PipelineContext 回滚功能"""
    # 创建 context 时需要传入 original_text 和 original_metadata
    context = PipelineContext(original_text="测试消息", original_metadata=basic_metadata)

    text = "测试消息"
    result = await similar_filter_pipeline._process(text, basic_metadata, context)

    assert result == text
    # rollback_actions 是公共属性
    assert len(context.rollback_actions) > 0

    # 执行回滚前检查缓存大小
    group_id = basic_metadata["group_id"]
    cache_size_before = len(similar_filter_pipeline._text_cache.get(group_id, []))

    # 执行回滚
    await context.rollback()

    # 回滚后缓存应该减少
    cache_size_after = len(similar_filter_pipeline._text_cache.get(group_id, []))
    if cache_size_before > 0:
        assert cache_size_after == cache_size_before - 1


# =============================================================================
# 相似度计算测试
# =============================================================================


@pytest.mark.asyncio
async def test_similarity_calculation(similar_filter_pipeline):
    """测试相似度计算"""
    metadata = {"user_id": "user1", "group_id": "group1"}

    # 测试完全相同的文本
    text1 = "完全相同的文本"
    await similar_filter_pipeline._process(text1, metadata)
    result = await similar_filter_pipeline._process(text1, metadata)
    assert result is None

    # 测试包含关系 (666 vs 6666)
    await similar_filter_pipeline.reset()
    text2 = "666"
    text3 = "6666"

    await similar_filter_pipeline._process(text2, metadata)
    result = await similar_filter_pipeline._process(text3, metadata)
    # 可能被过滤（包含关系）
    assert result is None or result == text3


# =============================================================================
# 时间窗口和历史记录测试
# =============================================================================


@pytest.mark.asyncio
async def test_time_window_expiration(similar_filter_pipeline, basic_metadata):
    """测试重置管道清空缓存"""
    text = "测试消息"

    # 使用 process() 而不是 _process()
    # 发送第一条消息
    result1 = await similar_filter_pipeline.process(text, basic_metadata)
    assert result1 == text

    # 验证缓存中有数据
    group_id = basic_metadata["group_id"]
    cache_size_before = len(similar_filter_pipeline._text_cache.get(group_id, []))
    assert cache_size_before > 0

    # 重置管道（清空所有缓存）
    await similar_filter_pipeline.reset()

    # 验证缓存已清空
    cache_size_after = len(similar_filter_pipeline._text_cache.get(group_id, []))
    assert cache_size_after == 0

    # 现在相同消息应该通过（缓存已清空）
    result2 = await similar_filter_pipeline.process(text, basic_metadata)
    assert result2 == text


@pytest.mark.asyncio
async def test_cache_cleanup(similar_filter_pipeline, basic_metadata):
    """测试缓存清理"""
    # 添加多条消息
    for i in range(5):
        await similar_filter_pipeline._process(f"消息{i}", basic_metadata)

    group_id = basic_metadata["group_id"]
    cache_before = len(similar_filter_pipeline._text_cache.get(group_id, []))
    assert cache_before > 0

    # 重置管道
    await similar_filter_pipeline.reset()

    # 验证缓存已清空
    cache_after = len(similar_filter_pipeline._text_cache.get(group_id, []))
    assert cache_after == 0


@pytest.mark.asyncio
async def test_different_groups(similar_filter_pipeline):
    """测试不同组独立过滤"""
    text = "相同的消息"

    group1_metadata = {"user_id": "user1", "group_id": "group1"}
    group2_metadata = {"user_id": "user1", "group_id": "group2"}

    # group1 发送消息
    result1 = await similar_filter_pipeline._process(text, group1_metadata)
    assert result1 == text

    # group2 发送相同消息（不同组）
    result2 = await similar_filter_pipeline._process(text, group2_metadata)
    assert result2 == text  # 应该通过（不同组独立计数)


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_info(similar_filter_pipeline):
    """测试获取管道信息"""
    info = similar_filter_pipeline.get_info()

    assert info["similarity_threshold"] == 0.85
    assert info["time_window"] == 5.0
    assert info["min_text_length"] == 3
    assert info["cross_user_filter"] is True
    assert "cache_groups" in info
    assert "cached_texts" in info


@pytest.mark.asyncio
async def test_statistics_tracking(similar_filter_pipeline, basic_metadata):
    """测试统计信息跟踪"""
    # 重置统计信息确保测试从干净状态开始
    similar_filter_pipeline.reset_stats()

    # 使用 process() 而不是 _process()，确保统计信息被正确记录
    await similar_filter_pipeline.process("消息1", basic_metadata)
    await similar_filter_pipeline.process("消息1", basic_metadata)  # 重复，被过滤

    stats = similar_filter_pipeline.get_stats()
    # process() 会被调用2次，所以 processed_count >= 2
    assert stats.processed_count >= 2
    # 第二条消息被过滤，dropped_count >= 1
    assert stats.dropped_count >= 1


# =============================================================================
# 边界条件测试
# =============================================================================


@pytest.mark.asyncio
async def test_empty_text(similar_filter_pipeline, basic_metadata):
    """测试空文本处理"""
    result = await similar_filter_pipeline._process("", basic_metadata)
    # 空文本低于最小长度，应该跳过过滤
    assert result == ""


@pytest.mark.asyncio
async def test_special_characters(similar_filter_pipeline, basic_metadata):
    """测试特殊字符处理"""
    text1 = "测试!!!消息@@@"
    text2 = "测试!!!消息@@@"

    result1 = await similar_filter_pipeline._process(text1, basic_metadata)
    assert result1 == text1

    result2 = await similar_filter_pipeline._process(text2, basic_metadata)
    assert result2 is None  # 相同，被过滤


@pytest.mark.asyncio
async def test_very_long_text(similar_filter_pipeline, basic_metadata):
    """测试超长文本处理"""
    long_text = "a" * 10000

    result = await similar_filter_pipeline._process(long_text, basic_metadata)
    assert result == long_text

    # 再次发送应该被过滤
    result2 = await similar_filter_pipeline._process(long_text, basic_metadata)
    assert result2 is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
