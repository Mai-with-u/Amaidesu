"""测试 OutputPipeline 协议"""

import asyncio

import pytest

from src.modules.pipeline import Pipeline
from src.modules.types.base.pipeline_types import PipelineErrorHandling, PipelineException
from src.modules.types import Intent, IntentMetadata


def create_test_intent(response_text: str = "测试响应") -> Intent:
    return Intent(
        metadata=IntentMetadata(
            source_id="test_source",
            decision_time=0,
        ),
        emotion="neutral",
        speech=response_text,
    )


class SimpleTestPipeline(Pipeline["Intent"]):
    async def _process(self, item: "Intent"):
        return item


class ModifyingTestPipeline(Pipeline["Intent"]):
    async def _process(self, item: "Intent"):
        item.speech = (item.speech or "") + " [已修改]"
        return item


class DroppingTestPipeline(Pipeline["Intent"]):
    async def _process(self, item: "Intent"):
        return None


class FailingTestPipeline(Pipeline["Intent"]):
    async def _process(self, item: "Intent"):
        raise ValueError("测试异常")


def test_pipeline_creation():
    pipeline = SimpleTestPipeline(config={})

    assert pipeline is not None
    assert pipeline.priority == 500
    assert pipeline.enabled is True
    assert pipeline.error_handling == PipelineErrorHandling.CONTINUE
    assert pipeline.timeout_seconds == 5.0


def test_pipeline_custom_config():
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
    config = {"error_handling": "invalid_value"}

    pipeline = SimpleTestPipeline(config=config)

    assert pipeline.error_handling == PipelineErrorHandling.CONTINUE


@pytest.mark.asyncio
async def test_process_success():
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is not None
    assert result.speech == "测试消息"


@pytest.mark.asyncio
async def test_process_modification():
    pipeline = ModifyingTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is not None
    assert result.speech == "测试消息 [已修改]"


@pytest.mark.asyncio
async def test_process_dropping():
    pipeline = DroppingTestPipeline(config={})

    intent = create_test_intent("测试消息")
    result = await pipeline.process(intent)

    assert result is None


@pytest.mark.asyncio
async def test_process_exception():
    pipeline = FailingTestPipeline(config={"error_handling": "continue"})

    intent = create_test_intent("测试消息")

    with pytest.raises(ValueError, match="测试异常"):
        await pipeline.process(intent)


@pytest.mark.asyncio
async def test_statistics_processing():
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    for _ in range(5):
        await pipeline.process(intent)

    stats = pipeline.get_stats()
    assert stats.processed_count == 5


@pytest.mark.asyncio
async def test_statistics_error():
    pipeline = FailingTestPipeline(config={"error_handling": "continue"})

    intent = create_test_intent()

    for _ in range(3):
        try:
            await pipeline.process(intent)
        except ValueError:
            pass

    stats = pipeline.get_stats()
    assert stats.error_count == 3


@pytest.mark.asyncio
async def test_statistics_average_duration():
    import time

    start = time.perf_counter()

    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    for _ in range(3):
        await pipeline.process(intent)

    await asyncio.sleep(0.05)

    end = time.perf_counter()
    elapsed = (end - start) * 1000

    stats = pipeline.get_stats()
    assert stats.processed_count == 3
    assert stats.avg_duration_ms >= 0


def test_get_info():
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


@pytest.mark.asyncio
async def test_reset_stats():
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()

    for _ in range(5):
        await pipeline.process(intent)

    stats_before = pipeline.get_stats()
    assert stats_before.processed_count == 5

    pipeline.reset_stats()

    stats_after = pipeline.get_stats()
    assert stats_after.processed_count == 0
    assert stats_after.total_duration_ms == 0


def test_error_handling_enum():
    assert PipelineErrorHandling.CONTINUE == "continue"
    assert PipelineErrorHandling.STOP == "stop"
    assert PipelineErrorHandling.DROP == "drop"


def test_pipeline_exception_creation():
    exc = PipelineException("test_pipeline", "测试错误")

    assert str(exc) == "[test_pipeline] 测试错误"
    assert exc.pipeline_name == "test_pipeline"
    assert exc.message == "测试错误"
    assert exc.original_error is None


def test_pipeline_exception_with_original_error():
    original = ValueError("原始错误")
    exc = PipelineException("test_pipeline", "包装错误", original_error=original)

    assert exc.original_error == original


def test_pipeline_exception_chaining():
    original = ValueError("原始错误")
    exc = PipelineException("test_pipeline", "包装错误", original_error=original)

    assert exc.__cause__ == original


def test_simple_test_pipeline_is_output_pipeline():
    pipeline = SimpleTestPipeline(config={})

    assert hasattr(pipeline, "priority")
    assert hasattr(pipeline, "enabled")
    assert hasattr(pipeline, "error_handling")
    assert hasattr(pipeline, "timeout_seconds")

    assert hasattr(pipeline, "process")
    assert callable(pipeline.process)

    assert hasattr(pipeline, "get_info")
    assert callable(pipeline.get_info)

    assert hasattr(pipeline, "get_stats")
    assert callable(pipeline.get_stats)

    assert hasattr(pipeline, "reset_stats")
    assert callable(pipeline.reset_stats)


@pytest.mark.asyncio
async def test_process_empty_intent():
    pipeline = SimpleTestPipeline(config={})

    intent = create_test_intent()
    result = await pipeline.process(intent)

    assert result is not None


@pytest.mark.asyncio
async def test_process_large_intent():
    pipeline = SimpleTestPipeline(config={})

    long_text = "a" * 100000
    intent = create_test_intent(long_text)
    result = await pipeline.process(intent)

    assert result is not None
    assert result.speech == long_text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])