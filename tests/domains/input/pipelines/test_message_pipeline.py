"""MessagePipeline 测试"""

import pytest
from src.domains.input.pipelines.manager import (
    InputPipelineManager,
    MessagePipelineBase,
)
from src.modules.types.base.normalized_message import NormalizedMessage


class MockMessagePipeline(MessagePipelineBase):
    """测试用的 Mock Pipeline"""

    priority = 100

    def __init__(self, config=None):
        super().__init__(config or {})
        self._should_drop = config.get("should_drop", False) if config else False

    async def _process(self, message: NormalizedMessage):
        if self._should_drop:
            return None
        return message


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


@pytest.fixture
def pipeline_manager():
    return InputPipelineManager()


@pytest.fixture
def mock_pipeline():
    return MockMessagePipeline()


@pytest.mark.asyncio
async def test_process_without_pipelines(pipeline_manager):
    """测试没有 Pipeline 时消息正常通过"""
    message = create_message("hello")
    result = await pipeline_manager.process(message)
    assert result is not None
    assert result.text == "hello"


@pytest.mark.asyncio
async def test_process_with_pipeline(pipeline_manager, mock_pipeline):
    """测试 Pipeline 注册后正常工作"""
    pipeline_manager.register_message_pipeline(mock_pipeline)
    message = create_message("hello")
    result = await pipeline_manager.process(message)
    assert result is not None
    assert result.text == "hello"


@pytest.mark.asyncio
async def test_process_drops_message(pipeline_manager):
    """测试 Pipeline 可以丢弃消息"""
    drop_pipeline = MockMessagePipeline({"should_drop": True})
    pipeline_manager.register_message_pipeline(drop_pipeline)
    message = create_message("hello")
    result = await pipeline_manager.process(message)
    assert result is None


@pytest.mark.asyncio
async def test_process_handles_raw_none(pipeline_manager, mock_pipeline):
    """测试 raw=None 边界情况"""
    pipeline_manager.register_message_pipeline(mock_pipeline)
    message = NormalizedMessage(
        text="test",
        source="test",
        data_type="text",
        raw=None,
    )
    result = await pipeline_manager.process(message)
    assert result is not None


@pytest.mark.asyncio
async def test_pipeline_priority_ordering(pipeline_manager):
    """测试 Pipeline 按优先级排序"""
    low_priority = MockMessagePipeline({"priority": 500})
    low_priority.priority = 500
    high_priority = MockMessagePipeline({"priority": 100})
    high_priority.priority = 100

    # 先注册低优先级
    pipeline_manager.register_message_pipeline(low_priority)
    # 再注册高优先级
    pipeline_manager.register_message_pipeline(high_priority)

    # 检查排序
    pipeline_manager._ensure_message_pipelines_sorted()
    assert pipeline_manager._message_pipelines[0].priority == 100
    assert pipeline_manager._message_pipelines[1].priority == 500
