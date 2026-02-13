"""
测试 OutputPipelineBase (输出管道基类)

运行: uv run pytest tests/domains/output/pipelines/test_base_pipeline.py -v
"""

import pytest

from src.domains.output.pipelines.base import (
    OutputPipelineBase,
    PipelineErrorHandling,
    PipelineException,
)
from src.modules.types import ActionType, EmotionType, Intent, IntentAction

# =============================================================================
# 测试用 Pipeline
# =============================================================================


def create_test_intent(response_text: str = "测试响应") -> Intent:
    """创建测试用的 Intent"""
    return Intent(
        original_text="测试输入",
        response_text=response_text,
        actions=[IntentAction(type=ActionType.NONE)],
        emotion=EmotionType.NEUTRAL,
    )


class SimpleTestPipeline(OutputPipelineBase):
    """简单的测试 Pipeline"""

    async def _process(self, intent: Intent):
        """直接返回 Intent"""
        return intent


class ModifyingTestPipeline(OutputPipelineBase):
    """修改 Intent 的测试 Pipeline"""

    async def _process(self, intent: Intent):
        """修改响应文本"""
        intent.response_text = intent.response_text + " [已修改]"
        return intent


class DroppingTestPipeline(OutputPipelineBase):
    """丢弃 Intent 的测试 Pipeline"""

    async def _process(self, intent: Intent):
        """返回 None 表示丢弃"""
        return None


class FailingTestPipeline(OutputPipelineBase):
    """抛出异常的测试 Pipeline"""

    async def _process(self, intent: Intent):
        """总是抛出异常"""
        raise ValueError("测试异常")


# =============================================================================
# 创建和初始化测试
# =============================================================================


def test_pipeline_creation():
    """测试 Pipeline 创建"""
    pipeline = SimpleTestPipeline(config={})

    assert pipeline is not None
    assert pipeline.priority == 500  # 默认优先级
    assert pipeline.enabled is True
    assert pipeline.error_handling == PipelineErrorHandling.CONTINUE
    assert pipeline.timeout_seconds == 5.0


def test_pipeline_custom_config():
    """测试自定义配置"""
    config = {
        "priority": 100,
        "enabled": False,
        "error_handling": "stop",
        "timeout_seconds": 10.0,
    }

    pipeline = SimpleTestPipeline(config=config)

    assert pipeline.priority == 100
    assert pipeline.enabled is False
    assert pipeline.error_handling == PipelineErrorHandling.STOP
    assert pipeline.timeout_seconds == 10.0


def test_pipeline_invalid_error_handling():
    """测试无效的 error_handling 值"""
    config = {"error_handling": "invalid_value"}

    pipeline = SimpleTestPipeline(config=config)

    # 应该回退到默认值
    assert pipeline.error_handling == PipelineErrorHandling.CONTINUE


# =============================================================================
# process() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_success():
    """测试成功处理"""
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is not None
    assert result.response_text == "测试消息"


@pytest.mark.asyncio
async def test_process_modification():
    """测试参数修改"""
    pipeline = ModifyingTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is not None
    assert result.response_text == "测试消息 [已修改]"


@pytest.mark.asyncio
async def test_process_dropping():
    """测试丢弃参数"""
    pipeline = DroppingTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is None


@pytest.mark.asyncio
async def test_process_exception():
    """测试处理异常"""
    pipeline = FailingTestPipeline(config={"error_handling": "continue"})

    intent = create_test_intent("测试消息")

    with pytest.raises(ValueError, match="测试异常"):
        await pipeline.process(intent)


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_statistics_processing():
    """测试处理计数"""
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    # 处理多次
    for _ in range(5):
        await pipeline.process(intent)

    stats = pipeline.get_stats()
    assert stats.processed_count == 5


@pytest.mark.asyncio
async def test_statistics_error():
    """测试错误计数"""
    pipeline = FailingTestPipeline(config={"error_handling": "continue"})

    intent = create_test_intent()

    # 处理多次（都会失败）
    for _ in range(3):
        try:
            await pipeline.process(intent)
        except ValueError:
            pass

    stats = pipeline.get_stats()
    assert stats.error_count == 3


@pytest.mark.asyncio
async def test_statistics_average_duration():
    """测试平均处理时间"""
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    # 处理几次
    for _ in range(3):
        await pipeline.process(intent)

    stats = pipeline.get_stats()
    assert stats.processed_count == 3
    assert stats.avg_duration_ms > 0


# =============================================================================
# get_info() 测试
# =============================================================================


def test_get_info():
    """测试获取 Pipeline 信息"""
    config = {
        "priority": 100,
        "enabled": False,
        "error_handling": "stop",
        "timeout_seconds": 10.0,
    }

    pipeline = SimpleTestPipeline(config=config)
    info = pipeline.get_info()

    assert info["name"] == "SimpleTestPipeline"
    assert info["priority"] == 100
    assert info["enabled"] is False
    assert info["error_handling"] == "stop"
    assert info["timeout_seconds"] == 10.0


# =============================================================================
# reset_stats() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_reset_stats():
    """测试重置统计信息"""
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    # 处理几次
    for _ in range(5):
        await pipeline.process(intent)

    stats_before = pipeline.get_stats()
    assert stats_before.processed_count == 5

    # 重置
    pipeline.reset_stats()

    stats_after = pipeline.get_stats()
    assert stats_after.processed_count == 0
    assert stats_after.total_duration_ms == 0


# =============================================================================
# PipelineErrorHandling 测试
# =============================================================================


def test_error_handling_enum():
    """测试错误处理枚举"""
    assert PipelineErrorHandling.CONTINUE == "continue"
    assert PipelineErrorHandling.STOP == "stop"
    assert PipelineErrorHandling.DROP == "drop"


# =============================================================================
# PipelineException 测试
# =============================================================================


def test_pipeline_exception_creation():
    """测试 PipelineException 创建"""
    exc = PipelineException("test_pipeline", "测试错误")

    assert str(exc) == "[test_pipeline] 测试错误"
    assert exc.pipeline_name == "test_pipeline"
    assert exc.message == "测试错误"
    assert exc.original_error is None


def test_pipeline_exception_with_original_error():
    """测试带原始错误的 PipelineException"""
    original = ValueError("原始错误")
    exc = PipelineException("test_pipeline", "包装错误", original_error=original)

    assert exc.original_error == original


def test_pipeline_exception_chaining():
    """测试异常链"""
    original = ValueError("原始错误")
    exc = PipelineException("test_pipeline", "包装错误", original_error=original)

    # 验证异常链
    assert exc.__cause__ == original


# =============================================================================
# OutputPipeline Protocol 测试
# =============================================================================


def test_simple_test_pipeline_is_output_pipeline():
    """测试 SimpleTestPipeline 符合 OutputPipeline Protocol"""
    pipeline = SimpleTestPipeline(config={})

    # 检查必需属性
    assert hasattr(pipeline, "priority")
    assert hasattr(pipeline, "enabled")
    assert hasattr(pipeline, "error_handling")
    assert hasattr(pipeline, "timeout_seconds")

    # 检查必需方法
    assert hasattr(pipeline, "process")
    assert callable(pipeline.process)

    assert hasattr(pipeline, "get_info")
    assert callable(pipeline.get_info)

    assert hasattr(pipeline, "get_stats")
    assert callable(pipeline.get_stats)

    assert hasattr(pipeline, "reset_stats")
    assert callable(pipeline.reset_stats)


# =============================================================================
# 边界条件测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_empty_intent():
    """测试处理空 Intent"""
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()  # 默认 Intent
    result = await pipeline.process(intent)

    assert result is not None


@pytest.mark.asyncio
async def test_process_large_intent():
    """测试处理大型 Intent"""
    pipeline = SimpleTestPipeline(config={})

    long_text = "a" * 100000
    intent = create_test_intent(long_text)
    result = await pipeline.process(intent)

    assert result is not None
    assert result.response_text == long_text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
