"""
测试 OutputPipelineManager (输出管道管理器)

运行: uv run pytest tests/domains/output/pipelines/test_manager.py -v
"""

import pytest
import asyncio

from src.domains.output.pipelines.manager import OutputPipelineManager
from src.domains.output.pipelines.base import (
    OutputPipelineBase,
    PipelineException,
)
from src.domains.output.parameters.render_parameters import ExpressionParameters


# =============================================================================
# 测试用 Pipeline
# =============================================================================


class PassThroughPipeline(OutputPipelineBase):
    """透传 Pipeline"""

    async def _process(self, params):
        return params


class ModifyingPipeline(OutputPipelineBase):
    """修改 Pipeline"""

    async def _process(self, params):
        params.tts_text += " [已修改]"
        return params


class DroppingPipeline(OutputPipelineBase):
    """丢弃 Pipeline"""

    async def _process(self, params):
        return None


class FailingPipeline(OutputPipelineBase):
    """失败 Pipeline"""

    async def _process(self, params):
        raise ValueError("测试失败")


class SlowPipeline(OutputPipelineBase):
    """慢速 Pipeline（用于超时测试）"""

    async def _process(self, params):
        await asyncio.sleep(10)  # 超过默认超时时间
        return params


# =============================================================================
# 创建和初始化测试
# =============================================================================


def test_manager_creation():
    """测试管理器创建"""
    manager = OutputPipelineManager()

    assert manager is not None
    assert len(manager._pipelines) == 0


# =============================================================================
# register_pipeline() 测试
# =============================================================================


def test_register_pipeline():
    """测试注册 Pipeline"""
    manager = OutputPipelineManager()
    pipeline = PassThroughPipeline(config={})

    manager.register_pipeline(pipeline)

    assert len(manager._pipelines) == 1
    assert manager._pipelines[0] == pipeline
    assert manager._pipelines_sorted is False


def test_register_multiple_pipelines():
    """测试注册多个 Pipeline"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = ModifyingPipeline(config={"priority": 200})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)

    assert len(manager._pipelines) == 2


# =============================================================================
# process() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_no_pipelines():
    """测试没有 Pipeline 时直接返回"""
    manager = OutputPipelineManager()

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    assert result == params


@pytest.mark.asyncio
async def test_process_single_pipeline():
    """测试单个 Pipeline 处理"""
    manager = OutputPipelineManager()
    pipeline = PassThroughPipeline(config={})
    manager.register_pipeline(pipeline)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    assert result is not None
    assert result.tts_text == "测试"


@pytest.mark.asyncio
async def test_process_multiple_pipelines():
    """测试多个 Pipeline 顺序处理"""
    manager = OutputPipelineManager()

    pipeline1 = ModifyingPipeline(config={"priority": 100})
    pipeline2 = ModifyingPipeline(config={"priority": 200})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    # 应该被修改两次（顺序根据优先级）
    assert result.tts_text.endswith(" [已修改] [已修改]")


@pytest.mark.asyncio
async def test_process_disabled_pipeline():
    """测试禁用的 Pipeline 不执行"""
    manager = OutputPipelineManager()

    pipeline = ModifyingPipeline(config={"enabled": False})
    manager.register_pipeline(pipeline)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    # 不应该被修改
    assert result.tts_text == "测试"


@pytest.mark.asyncio
async def test_process_dropping_pipeline():
    """测试丢弃 Pipeline"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = DroppingPipeline(config={"priority": 200})
    pipeline3 = ModifyingPipeline(config={"priority": 300})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)
    manager.register_pipeline(pipeline3)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    # pipeline2 丢弃后，后续 pipeline 不执行
    assert result is None


@pytest.mark.asyncio
async def test_process_priority_ordering():
    """测试按优先级排序执行"""
    manager = OutputPipelineManager()

    # 逆序注册
    pipeline3 = PassThroughPipeline(config={"priority": 300})
    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = PassThroughPipeline(config={"priority": 200})

    manager.register_pipeline(pipeline3)
    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)

    # 验证排序
    manager._ensure_pipelines_sorted()

    assert manager._pipelines[0].priority == 100
    assert manager._pipelines[1].priority == 200
    assert manager._pipelines[2].priority == 300


# =============================================================================
# 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_continue_on_error():
    """测试错误处理策略 CONTINUE"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = FailingPipeline(config={"priority": 200, "error_handling": "continue"})
    pipeline3 = ModifyingPipeline(config={"priority": 300})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)
    manager.register_pipeline(pipeline3)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    # pipeline2 失败但继续执行 pipeline3
    assert result is not None
    assert result.tts_text.endswith(" [已修改]")


@pytest.mark.asyncio
async def test_process_stop_on_error():
    """测试错误处理策略 STOP"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = FailingPipeline(config={"priority": 200, "error_handling": "stop"})
    pipeline3 = ModifyingPipeline(config={"priority": 300})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)
    manager.register_pipeline(pipeline3)

    params = ExpressionParameters(tts_text="测试")

    with pytest.raises(PipelineException):
        await manager.process(params)


@pytest.mark.asyncio
async def test_process_drop_on_error():
    """测试错误处理策略 DROP"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = FailingPipeline(config={"priority": 200, "error_handling": "drop"})
    pipeline3 = ModifyingPipeline(config={"priority": 300})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)
    manager.register_pipeline(pipeline3)

    params = ExpressionParameters(tts_text="测试")
    result = await manager.process(params)

    # pipeline2 失败后丢弃
    assert result is None


@pytest.mark.asyncio
async def test_process_timeout():
    """测试超时处理"""
    manager = OutputPipelineManager()

    pipeline = SlowPipeline(config={"timeout_seconds": 0.1})
    manager.register_pipeline(pipeline)

    params = ExpressionParameters(tts_text="测试")

    with pytest.raises(PipelineException, match="超时"):
        await manager.process(params)


# =============================================================================
# process_parameters() 测试（向后兼容）
# =============================================================================


@pytest.mark.asyncio
async def test_process_parameters_compatibility():
    """测试 process_parameters 向后兼容性"""
    manager = OutputPipelineManager()

    pipeline = PassThroughPipeline(config={})
    manager.register_pipeline(pipeline)

    params = ExpressionParameters(tts_text="测试")
    metadata = {"source": "test"}

    result = await manager.process_parameters(params, metadata)

    assert result is not None


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_pipeline_stats():
    """测试获取 Pipeline 统计信息"""
    manager = OutputPipelineManager()

    pipeline1 = PassThroughPipeline(config={"priority": 100})
    pipeline2 = DroppingPipeline(config={"priority": 200, "enabled": False})

    manager.register_pipeline(pipeline1)
    manager.register_pipeline(pipeline2)

    # 处理一些数据
    params = ExpressionParameters(tts_text="测试")
    await manager.process(params)

    stats = manager.get_pipeline_stats()

    assert "PassThroughPipeline" in stats
    assert stats["PassThroughPipeline"]["enabled"] is True
    assert stats["PassThroughPipeline"]["priority"] == 100

    assert "DroppingPipeline" in stats
    assert stats["DroppingPipeline"]["enabled"] is False


# =============================================================================
# 并发测试
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_processing():
    """测试并发处理"""
    manager = OutputPipelineManager()

    pipeline = PassThroughPipeline(config={})
    manager.register_pipeline(pipeline)

    # 并发处理多个参数
    tasks = []
    for i in range(10):
        params = ExpressionParameters(tts_text=f"测试{i}")
        task = manager.process(params)
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # 所有处理都应该成功
    assert all(r is not None for r in results)
    assert len(results) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
