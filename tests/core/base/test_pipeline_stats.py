"""
PipelineStats 单元测试

测试 PipelineStats 数据类的所有核心功能：
- 数据类创建和基本属性
- 字段默认值
- avg_duration_ms 属性计算
- 序列化

运行: uv run pytest tests/core/base/test_pipeline_stats.py -v
"""

import json

import pytest

from src.core.base.pipeline_stats import PipelineStats


# =============================================================================
# 实例创建和基本属性测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_stats_creation():
    """测试创建 PipelineStats 实例"""
    stats = PipelineStats(
        processed_count=10,
        dropped_count=2,
        error_count=1,
        total_duration_ms=150.5,
        filtered_words_count=5,
    )

    assert stats.processed_count == 10
    assert stats.dropped_count == 2
    assert stats.error_count == 1
    assert stats.total_duration_ms == 150.5
    assert stats.filtered_words_count == 5


@pytest.mark.asyncio
async def test_pipeline_stats_creation_partial_fields():
    """测试创建包含部分字段的 PipelineStats 实例"""
    stats = PipelineStats(
        processed_count=5,
        total_duration_ms=50.0,
    )

    assert stats.processed_count == 5
    assert stats.total_duration_ms == 50.0
    # 其他字段应该使用默认值
    assert stats.dropped_count == 0
    assert stats.error_count == 0
    assert stats.filtered_words_count == 0


# =============================================================================
# 字段默认值测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_stats_default_values():
    """测试所有字段默认值为 0"""
    stats = PipelineStats()

    assert stats.processed_count == 0
    assert stats.dropped_count == 0
    assert stats.error_count == 0
    assert stats.total_duration_ms == 0.0
    assert stats.filtered_words_count == 0


@pytest.mark.asyncio
async def test_pipeline_stats_processed_count_default():
    """测试 processed_count 默认值为 0"""
    stats = PipelineStats()

    assert stats.processed_count == 0


@pytest.mark.asyncio
async def test_pipeline_stats_dropped_count_default():
    """测试 dropped_count 默认值为 0"""
    stats = PipelineStats()

    assert stats.dropped_count == 0


@pytest.mark.asyncio
async def test_pipeline_stats_error_count_default():
    """测试 error_count 默认值为 0"""
    stats = PipelineStats()

    assert stats.error_count == 0


@pytest.mark.asyncio
async def test_pipeline_stats_total_duration_ms_default():
    """测试 total_duration_ms 默认值为 0.0"""
    stats = PipelineStats()

    assert stats.total_duration_ms == 0.0


@pytest.mark.asyncio
async def test_pipeline_stats_filtered_words_count_default():
    """测试 filtered_words_count 默认值为 0"""
    stats = PipelineStats()

    assert stats.filtered_words_count == 0


# =============================================================================
# avg_duration_ms 属性计算测试
# =============================================================================


@pytest.mark.asyncio
async def test_avg_duration_ms_calculation():
    """测试 avg_duration_ms 正确计算平均值"""
    stats = PipelineStats(
        processed_count=10,
        total_duration_ms=150.0,
    )

    # 平均值 = 150.0 / 10 = 15.0
    assert stats.avg_duration_ms == 15.0


@pytest.mark.asyncio
async def test_avg_duration_ms_zero_processed():
    """测试 processed_count 为 0 时返回 0.0"""
    stats = PipelineStats(
        processed_count=0,
        total_duration_ms=100.0,
    )

    # 避免除零错误
    assert stats.avg_duration_ms == 0.0


@pytest.mark.asyncio
async def test_avg_duration_ms_single_item():
    """测试只有一个项目时的平均值"""
    stats = PipelineStats(
        processed_count=1,
        total_duration_ms=42.5,
    )

    assert stats.avg_duration_ms == 42.5


@pytest.mark.asyncio
async def test_avg_duration_ms_fractional_result():
    """测试平均值结果为小数"""
    stats = PipelineStats(
        processed_count=3,
        total_duration_ms=100.0,
    )

    # 100.0 / 3 = 33.333...
    assert abs(stats.avg_duration_ms - 33.333) < 0.001


@pytest.mark.asyncio
async def test_avg_duration_ms_large_numbers():
    """测试大数值计算"""
    stats = PipelineStats(
        processed_count=1000000,
        total_duration_ms=5000000.0,
    )

    # 5000000.0 / 1000000 = 5.0
    assert stats.avg_duration_ms == 5.0


@pytest.mark.asyncio
async def test_avg_duration_ms_updates_dynamically():
    """测试 avg_duration_ms 随数据更新而变化"""
    stats = PipelineStats(
        processed_count=10,
        total_duration_ms=100.0,
    )

    # 初始平均值 = 10.0
    assert stats.avg_duration_ms == 10.0

    # 更新数据
    stats.processed_count = 20
    stats.total_duration_ms = 250.0

    # 新平均值 = 12.5
    assert stats.avg_duration_ms == 12.5


# =============================================================================
# 序列化测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_stats_dataclass_to_dict():
    """测试 PipelineStats 转换为字典（使用 dataclasses.asdict）"""
    from dataclasses import asdict

    stats = PipelineStats(
        processed_count=15,
        dropped_count=3,
        error_count=1,
        total_duration_ms=200.0,
        filtered_words_count=10,
    )

    stats_dict = asdict(stats)

    assert stats_dict["processed_count"] == 15
    assert stats_dict["dropped_count"] == 3
    assert stats_dict["error_count"] == 1
    assert stats_dict["total_duration_ms"] == 200.0
    assert stats_dict["filtered_words_count"] == 10


@pytest.mark.asyncio
async def test_pipeline_stats_json_serialization():
    """测试 PipelineStats JSON 序列化"""
    from dataclasses import asdict

    stats = PipelineStats(
        processed_count=5,
        dropped_count=1,
        error_count=0,
        total_duration_ms=75.5,
    )

    stats_dict = asdict(stats)
    json_str = json.dumps(stats_dict)
    parsed = json.loads(json_str)

    assert parsed["processed_count"] == 5
    assert parsed["dropped_count"] == 1
    assert parsed["error_count"] == 0
    assert parsed["total_duration_ms"] == 75.5


# =============================================================================
# 字段修改测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_stats_mutability():
    """测试 PipelineStats 字段可修改"""
    stats = PipelineStats()

    # 修改字段
    stats.processed_count = 10
    stats.dropped_count = 2
    stats.error_count = 1
    stats.total_duration_ms = 150.0
    stats.filtered_words_count = 5

    assert stats.processed_count == 10
    assert stats.dropped_count == 2
    assert stats.error_count == 1
    assert stats.total_duration_ms == 150.0
    assert stats.filtered_words_count == 5


@pytest.mark.asyncio
async def test_pipeline_stats_increment_updates():
    """测试增量更新统计信息"""
    stats = PipelineStats()

    # 初始状态
    assert stats.processed_count == 0
    assert stats.total_duration_ms == 0.0

    # 模拟处理多个项目
    stats.processed_count += 1
    stats.total_duration_ms += 10.5

    stats.processed_count += 1
    stats.total_duration_ms += 15.3

    stats.processed_count += 1
    stats.total_duration_ms += 8.2

    assert stats.processed_count == 3
    assert stats.total_duration_ms == 34.0
    assert stats.avg_duration_ms == 34.0 / 3


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_stats_negative_values():
    """测试可以设置负值（虽然不应该）"""
    stats = PipelineStats(
        processed_count=-1,
        dropped_count=-2,
        error_count=-3,
        total_duration_ms=-10.0,
    )

    assert stats.processed_count == -1
    assert stats.dropped_count == -2
    assert stats.error_count == -3
    assert stats.total_duration_ms == -10.0


@pytest.mark.asyncio
async def test_pipeline_stats_very_large_values():
    """测试大数值"""
    stats = PipelineStats(
        processed_count=999999999999,
        total_duration_ms=999999999999.999,
    )

    assert stats.processed_count == 999999999999
    assert stats.total_duration_ms == 999999999999.999


@pytest.mark.asyncio
async def test_pipeline_stats_zero_duration():
    """测试总时间为 0 的情况"""
    stats = PipelineStats(
        processed_count=10,
        total_duration_ms=0.0,
    )

    # 平均时间 = 0.0 / 10 = 0.0
    assert stats.avg_duration_ms == 0.0


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
