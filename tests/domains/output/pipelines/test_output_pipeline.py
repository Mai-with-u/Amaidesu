"""
测试 OutputPipeline 接口和基类
"""

import pytest

from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.domains.output.pipelines import (
    OutputPipelineBase,
    OutputPipelineManager,
    PipelineErrorHandling,
    PipelineException,
    PipelineStats,
)


class DummyOutputPipeline(OutputPipelineBase):
    """测试用的 OutputPipeline"""

    async def _process(self, params: ExpressionParameters):
        # 简单处理：添加元数据
        params.metadata["processed"] = True
        return params


class DropOutputPipeline(OutputPipelineBase):
    """测试用的丢弃 Pipeline"""

    async def _process(self, params: ExpressionParameters):
        # 丢弃所有输出
        return None


@pytest.mark.asyncio
async def test_output_pipeline_base():
    """测试 OutputPipelineBase 基本功能"""
    pipeline = DummyOutputPipeline(config={})

    # 测试 process 方法
    params = ExpressionParameters(tts_text="Hello")
    result = await pipeline.process(params)

    assert result is not None
    assert result.metadata.get("processed") is True
    assert result.tts_text == "Hello"

    # 测试统计信息
    stats = pipeline.get_stats()
    assert stats.processed_count == 1
    assert stats.dropped_count == 0
    assert stats.error_count == 0


@pytest.mark.asyncio
async def test_output_pipeline_drop():
    """测试 OutputPipeline 丢弃功能"""
    pipeline = DropOutputPipeline(config={})

    params = ExpressionParameters(tts_text="Hello")
    result = await pipeline.process(params)

    assert result is None

    stats = pipeline.get_stats()
    assert stats.processed_count == 1
    # 注意：丢弃统计在 manager 中处理，这里只测试返回 None


@pytest.mark.asyncio
async def test_output_pipeline_manager():
    """测试 OutputPipelineManager"""
    manager = OutputPipelineManager()

    # 注册管道
    pipeline1 = DummyOutputPipeline(config={"priority": 100})
    pipeline2 = DummyOutputPipeline(config={"priority": 200})
    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)

    # 测试处理
    params = ExpressionParameters(tts_text="Hello")
    result = await manager.process(params)

    assert result is not None
    assert result.tts_text == "Hello"
    # 两个管道都应该处理过（都添加了 metadata）
    # 但因为同名 key，最后只会有一个 True


@pytest.mark.asyncio
async def test_output_pipeline_manager_with_drop():
    """测试 OutputPipelineManager 丢弃逻辑"""
    manager = OutputPipelineManager()

    # 注册管道：第一个丢弃
    drop_pipeline = DropOutputPipeline(config={"priority": 100})
    dummy_pipeline = DummyOutputPipeline(config={"priority": 200})
    manager.register_pipeline(drop_pipeline)
    manager.register_pipeline(dummy_pipeline)

    # 测试处理
    params = ExpressionParameters(tts_text="Hello")
    result = await manager.process(params)

    # 第一个管道丢弃，返回 None
    assert result is None

    # 第二个管道不应该执行
    stats = dummy_pipeline.get_stats()
    assert stats.processed_count == 0


@pytest.mark.asyncio
async def test_output_pipeline_disabled():
    """测试禁用的 Pipeline"""
    manager = OutputPipelineManager()

    # 注册禁用的管道
    pipeline = DummyOutputPipeline(config={"enabled": False})
    manager.register_pipeline(pipeline)

    # 测试处理
    params = ExpressionParameters(tts_text="Hello")
    result = await manager.process(params)

    assert result is not None
    # 管道被禁用，不应该处理
    stats = pipeline.get_stats()
    assert stats.processed_count == 0


@pytest.mark.asyncio
async def test_output_pipeline_priority():
    """测试 Pipeline 优先级排序"""
    manager = OutputPipelineManager()

    # 创建不同的 Pipeline 类来测试优先级
    class PriorityPipeline1(OutputPipelineBase):
        async def _process(self, params):
            params.metadata["priority_1"] = True
            return params

    class PriorityPipeline2(OutputPipelineBase):
        async def _process(self, params):
            params.metadata["priority_2"] = True
            return params

    class PriorityPipeline3(OutputPipelineBase):
        async def _process(self, params):
            params.metadata["priority_3"] = True
            return params

    # 注册不同优先级的管道
    pipeline1 = PriorityPipeline1(config={"priority": 300})
    pipeline2 = PriorityPipeline2(config={"priority": 100})
    pipeline3 = PriorityPipeline3(config={"priority": 200})
    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)
    manager.register_pipeline(pipeline3)

    # 获取管道统计信息（包含优先级）
    stats = manager.get_pipeline_stats()

    # 验证所有管道都已注册
    assert len(stats) == 3

    # 验证优先级正确设置
    assert stats["PriorityPipeline1"]["priority"] == 300
    assert stats["PriorityPipeline2"]["priority"] == 100
    assert stats["PriorityPipeline3"]["priority"] == 200

    # 测试处理顺序：priority 100 -> 200 -> 300
    params = ExpressionParameters(tts_text="Hello")
    result = await manager.process(params)

    assert result is not None
    # 所有管道都应该执行
    assert result.metadata.get("priority_1") is True
    assert result.metadata.get("priority_2") is True
    assert result.metadata.get("priority_3") is True


def test_pipeline_stats():
    """测试 PipelineStats"""
    stats = PipelineStats()

    assert stats.processed_count == 0
    assert stats.dropped_count == 0
    assert stats.error_count == 0
    assert stats.avg_duration_ms == 0.0

    # 测试平均时间计算
    stats.processed_count = 10
    stats.total_duration_ms = 100.0
    assert stats.avg_duration_ms == 10.0


def test_pipeline_error_handling():
    """测试 PipelineErrorHandling 枚举"""
    assert PipelineErrorHandling.CONTINUE == "continue"
    assert PipelineErrorHandling.STOP == "stop"
    assert PipelineErrorHandling.DROP == "drop"


def test_pipeline_exception():
    """测试 PipelineException"""
    error = PipelineException("TestPipeline", "Test error")
    assert error.pipeline_name == "TestPipeline"
    assert error.message == "Test error"
    assert "TestPipeline" in str(error)
    assert "Test error" in str(error)
