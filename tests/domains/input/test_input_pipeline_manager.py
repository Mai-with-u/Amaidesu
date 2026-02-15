"""
InputPipelineManager 单元测试

测试 InputPipelineManager 的所有核心功能：
- InputPipeline 管理（注册、处理、优先级排序）
- 统计信息
- 错误处理
- 并发处理

运行: uv run pytest tests/domains/input/test_input_pipeline_manager.py -v
"""

import asyncio
from typing import Any, Dict, Optional

import pytest

from src.domains.input.pipelines.manager import (
    InputPipelineManager,
    InputPipelineBase,
    PipelineErrorHandling,
    PipelineException,
)
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.base.pipeline_stats import PipelineStats

# =============================================================================
# Mock Pipeline 实现
# =============================================================================


def create_message(text: str, user_id: str = "test_user", source: str = "test_source") -> NormalizedMessage:
    """创建测试用的 NormalizedMessage"""

    class MockRaw:
        open_id = user_id
        uname = "test_name"

    return NormalizedMessage(
        text=text,
        source=source,
        data_type="text",
        raw=MockRaw(),
    )


class MockInputPipeline(InputPipelineBase):
    """Mock InputPipeline 用于测试"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.processed_messages = []
        self.should_drop = False
        self.should_fail = False
        self.should_timeout = False
        self.delay_ms = 0

    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """处理消息"""
        self.processed_messages.append(message)

        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000)

        if self.should_timeout:
            await asyncio.sleep(10)  # 超时

        if self.should_fail:
            raise ValueError("Mock InputPipeline failure")

        if self.should_drop:
            return None

        return message


class SlowMockInputPipeline(InputPipelineBase):
    """慢速 Mock InputPipeline 用于测试超时"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sleep_time = config.get("sleep_time", 1.0)

    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        await asyncio.sleep(self.sleep_time)
        return message


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def pipeline_manager():
    """创建 InputPipelineManager 实例"""
    return InputPipelineManager()


@pytest.fixture
def sample_message():
    """创建示例消息"""
    return create_message("hello world")


# =============================================================================
# InputPipeline 注册测试
# =============================================================================


@pytest.mark.asyncio
async def test_register_pipeline(pipeline_manager: InputPipelineManager):
    """测试注册 InputPipeline"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_pipeline(pipeline)

    assert len(pipeline_manager._pipelines) == 1
    assert pipeline_manager._pipelines[0] == pipeline
    assert not pipeline_manager._pipelines_sorted


@pytest.mark.asyncio
async def test_register_multiple_pipelines(pipeline_manager: InputPipelineManager):
    """测试注册多个 InputPipeline"""
    pipeline1 = MockInputPipeline({})
    pipeline2 = MockInputPipeline({})
    pipeline3 = MockInputPipeline({})

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)
    pipeline_manager.register_pipeline(pipeline3)

    assert len(pipeline_manager._pipelines) == 3


# =============================================================================
# InputPipeline 处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_single_pipeline(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试单个 InputPipeline 处理消息"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline_manager.register_pipeline(pipeline)

    result = await pipeline_manager.process(sample_message)

    assert result is not None
    assert result.text == sample_message.text
    assert len(pipeline.processed_messages) == 1
    assert pipeline.processed_messages[0].text == sample_message.text


@pytest.mark.asyncio
async def test_process_multiple_pipelines(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试多个 InputPipeline 处理消息"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 50
    pipeline3 = MockInputPipeline({})
    pipeline3.priority = 150

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)
    pipeline_manager.register_pipeline(pipeline3)

    await pipeline_manager.process(sample_message)

    # 所有管道都应该处理
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1
    assert len(pipeline3.processed_messages) == 1


@pytest.mark.asyncio
async def test_process_pipeline_drops_message(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 丢弃消息"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 200
    pipeline2.should_drop = True

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    result = await pipeline_manager.process(sample_message)

    # pipeline2 丢弃消息，应返回 None
    assert result is None
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1

    # 验证丢弃计数
    stats2 = pipeline2.get_stats()
    assert stats2.dropped_count == 1


@pytest.mark.asyncio
async def test_process_empty_pipeline_list(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试空 InputPipeline 列表"""
    result = await pipeline_manager.process(sample_message)

    # 没有 Pipeline，应返回原消息
    assert result == sample_message


@pytest.mark.asyncio
async def test_process_disabled_pipeline(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试禁用的 InputPipeline 不处理"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 200
    pipeline2.enabled = False

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    await pipeline_manager.process(sample_message)

    # 只有 pipeline1 处理了
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 0


# =============================================================================
# InputPipeline 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_pipeline_error_continue(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 错误处理：CONTINUE"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    result = await pipeline_manager.process(sample_message)

    # pipeline2 失败但继续，pipeline1 应该已处理
    assert result is not None
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1

    # 验证错误计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count >= 1


@pytest.mark.asyncio
async def test_process_pipeline_error_stop(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 错误处理：STOP"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline.should_fail = True
    pipeline.error_handling = PipelineErrorHandling.STOP

    pipeline_manager.register_pipeline(pipeline)

    with pytest.raises(PipelineException) as exc_info:
        await pipeline_manager.process(sample_message)

    assert "MockInputPipeline" in str(exc_info.value)
    assert "处理失败" in str(exc_info.value)


@pytest.mark.asyncio
async def test_process_pipeline_error_drop(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 错误处理：DROP"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True
    pipeline2.error_handling = PipelineErrorHandling.DROP

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    result = await pipeline_manager.process(sample_message)

    # pipeline2 失败并丢弃消息
    assert result is None

    # 验证错误和丢弃计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count >= 1
    assert stats2.dropped_count == 1


@pytest.mark.asyncio
async def test_process_pipeline_timeout_continue(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 超时：CONTINUE"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = SlowMockInputPipeline({"sleep_time": 1.0, "timeout_seconds": 0.1})
    pipeline2.priority = 200
    pipeline2.timeout_seconds = 0.1
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    result = await pipeline_manager.process(sample_message)

    # pipeline2 超时但继续，pipeline1 应该已处理
    assert result is not None

    # 验证错误计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count == 1


@pytest.mark.asyncio
async def test_process_pipeline_timeout_drop(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 超时：DROP"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = SlowMockInputPipeline({"sleep_time": 1.0})
    pipeline2.priority = 200
    pipeline2.timeout_seconds = 0.1
    pipeline2.error_handling = PipelineErrorHandling.DROP

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    result = await pipeline_manager.process(sample_message)

    # pipeline2 超时并丢弃
    assert result is None

    # 验证错误和丢弃计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count == 1
    assert stats2.dropped_count == 1


# =============================================================================
# InputPipeline 优先级排序测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_priority_sorting(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试 InputPipeline 按优先级排序"""
    execution_order = []

    class OrderedInputPipeline(MockInputPipeline):
        def __init__(self, config: Dict[str, Any], name: str):
            super().__init__(config)
            self.name = name

        async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
            execution_order.append(self.name)
            return message

    pipeline1 = OrderedInputPipeline({}, "pipeline1")
    pipeline1.priority = 100
    pipeline2 = OrderedInputPipeline({}, "pipeline2")
    pipeline2.priority = 50
    pipeline3 = OrderedInputPipeline({}, "pipeline3")
    pipeline3.priority = 150

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)
    pipeline_manager.register_pipeline(pipeline3)

    await pipeline_manager.process(sample_message)

    # 验证执行顺序：50 -> 100 -> 150
    assert execution_order == ["pipeline2", "pipeline1", "pipeline3"]


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_stats(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试获取 InputPipeline 统计信息"""
    pipeline1 = MockInputPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockInputPipeline({})
    pipeline2.priority = 200
    pipeline2.enabled = False

    pipeline_manager.register_pipeline(pipeline1)
    pipeline_manager.register_pipeline(pipeline2)

    # 处理一些消息（只有 pipeline1 会处理，pipeline2 被禁用）
    await pipeline_manager.process(sample_message)
    await pipeline_manager.process(sample_message)

    stats = pipeline_manager.get_stats()

    # 验证基本结构
    assert len(stats) >= 1
    assert "MockInputPipeline" in stats
    assert "processed_count" in stats["MockInputPipeline"]
    assert "dropped_count" in stats["MockInputPipeline"]
    assert "error_count" in stats["MockInputPipeline"]
    assert "enabled" in stats["MockInputPipeline"]
    assert "priority" in stats["MockInputPipeline"]


@pytest.mark.asyncio
async def test_get_stats_with_drops(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试统计信息包含丢弃计数"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline.should_drop = True

    pipeline_manager.register_pipeline(pipeline)

    await pipeline_manager.process(sample_message)

    stats = pipeline_manager.get_stats()

    assert stats["MockInputPipeline"]["dropped_count"] == 1


@pytest.mark.asyncio
async def test_get_stats_with_errors(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试统计信息包含错误计数"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline.should_fail = True
    pipeline.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_pipeline(pipeline)

    await pipeline_manager.process(sample_message)

    stats = pipeline_manager.get_stats()

    assert stats["MockInputPipeline"]["error_count"] >= 1


@pytest.mark.asyncio
async def test_get_stats_avg_duration(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试统计信息包含平均处理时间"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline.delay_ms = 50  # 50ms 延迟

    pipeline_manager.register_pipeline(pipeline)

    await pipeline_manager.process(sample_message)
    await pipeline_manager.process(sample_message)

    stats = pipeline_manager.get_stats()

    assert stats["MockInputPipeline"]["processed_count"] == 2
    assert stats["MockInputPipeline"]["avg_duration_ms"] >= 50  # 至少 50ms


@pytest.mark.asyncio
async def test_get_stats_empty(pipeline_manager: InputPipelineManager):
    """测试空 InputPipeline 列表的统计"""
    stats = pipeline_manager.get_stats()

    assert len(stats) == 0


# =============================================================================
# PipelineStats 测试
# =============================================================================


def test_pipeline_stats_defaults():
    """测试 PipelineStats 默认值"""
    stats = PipelineStats()

    assert stats.processed_count == 0
    assert stats.dropped_count == 0
    assert stats.error_count == 0
    assert stats.total_duration_ms == 0
    assert stats.avg_duration_ms == 0


def test_pipeline_stats_avg_duration_no_processed():
    """测试没有处理记录时的平均时长"""
    stats = PipelineStats()

    assert stats.avg_duration_ms == 0


def test_pipeline_stats_avg_duration_with_processed():
    """测试计算平均处理时长"""
    stats = PipelineStats()
    stats.processed_count = 3
    stats.total_duration_ms = 150

    assert stats.avg_duration_ms == 50


# =============================================================================
# 并发测试
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_processing(pipeline_manager: InputPipelineManager, sample_message: NormalizedMessage):
    """测试并发处理多个消息"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100
    pipeline.delay_ms = 10

    pipeline_manager.register_pipeline(pipeline)

    # 并发处理 10 个消息
    messages = [create_message(f"text_{i}") for i in range(10)]
    tasks = [pipeline_manager.process(msg) for msg in messages]

    results = await asyncio.gather(*tasks)

    # 所有消息都应该被处理
    assert len(pipeline.processed_messages) == 10
    assert all(r is not None for r in results)


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_empty_text(pipeline_manager: InputPipelineManager):
    """测试处理空文本消息"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_pipeline(pipeline)

    message = create_message("")
    result = await pipeline_manager.process(message)

    assert result is not None
    assert len(pipeline.processed_messages) == 1
    assert pipeline.processed_messages[0].text == ""


@pytest.mark.asyncio
async def test_process_with_unicode(pipeline_manager: InputPipelineManager):
    """测试处理 Unicode 文本"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_pipeline(pipeline)

    unicode_text = "你好世界 🌍 Ñoño"
    message = create_message(unicode_text)
    result = await pipeline_manager.process(message)

    assert result is not None
    assert result.text == unicode_text


@pytest.mark.asyncio
async def test_process_handles_raw_none(pipeline_manager: InputPipelineManager):
    """测试 raw=None 边界情况"""
    pipeline = MockInputPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_pipeline(pipeline)

    message = NormalizedMessage(
        text="test",
        source="test",
        data_type="text",
        raw=None,
    )
    result = await pipeline_manager.process(message)
    assert result is not None


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
