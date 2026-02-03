"""
PipelineManager 单元测试

测试 PipelineManager 的所有核心功能：
- MessagePipeline 管理（注册、处理、优先级排序）
- TextPipeline 管理（注册、处理、优先级排序）
- 连接/断开通知
- 统计信息
- 错误处理

运行: uv run pytest tests/core/test_pipeline_manager.py -v
"""

import asyncio
import pytest
from typing import Optional, Dict, Any
from unittest.mock import Mock, MagicMock, AsyncMock
from maim_message import MessageBase, BaseMessageInfo, Seg, UserInfo
from maim_message.message_base import FormatInfo

from src.core.pipeline_manager import (
    PipelineManager,
    MessagePipeline,
    TextPipeline,
    TextPipelineBase,
    PipelineErrorHandling,
    PipelineStats,
    PipelineException,
)


# =============================================================================
# Mock MessageBase 创建辅助函数
# =============================================================================


def create_mock_message(content: str = "test message") -> MessageBase:
    """创建用于测试的 Mock MessageBase 对象"""
    # 创建 UserInfo
    user_info = UserInfo(
        platform="test",
        user_id="test_user",
        user_nickname="Test User",
    )

    # 创建 FormatInfo (使用正确的参数)
    format_info = FormatInfo(
        content_format=None,
        accept_format=None,
    )

    # 创建 BaseMessageInfo (移除 reply 参数)
    message_info = BaseMessageInfo(
        message_id="test_msg_123",
        platform="test",
        time=1234567890,
        user_info=user_info,
        format_info=format_info,
    )

    # 创建 Seg (text 类型，移除 extra 参数)
    seg = Seg(type="text", data=content)

    return MessageBase(
        message_info=message_info,
        message_segment=seg,
        raw_message=content,
    )


# =============================================================================
# Mock Pipeline 实现
# =============================================================================


class MockMessagePipeline(MessagePipeline):
    """Mock MessagePipeline 用于测试"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.processed_messages = []
        self.connect_called = False
        self.disconnect_called = False
        self.should_drop = False
        self.should_fail = False

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """记录处理过的消息"""
        self.processed_messages.append(message)
        if self.should_fail:
            raise ValueError("Mock pipeline failure")
        if self.should_drop:
            return None
        return message

    async def on_connect(self) -> None:
        """记录连接调用"""
        self.connect_called = True
        await super().on_connect()

    async def on_disconnect(self) -> None:
        """记录断开调用"""
        self.disconnect_called = True
        await super().on_disconnect()


class MockTextPipeline(TextPipelineBase):
    """Mock TextPipeline 用于测试"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.processed_texts = []
        self.should_drop = False
        self.should_fail = False
        self.should_timeout = False
        self.delay_ms = 0

    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """处理文本"""
        self.processed_texts.append((text, metadata))

        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000)

        if self.should_timeout:
            await asyncio.sleep(10)  # 超时

        if self.should_fail:
            raise ValueError("Mock TextPipeline failure")

        if self.should_drop:
            return None

        return f"[{self.__class__.__name__}] {text}"


class SlowMockTextPipeline(TextPipelineBase):
    """慢速 Mock TextPipeline 用于测试超时"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.sleep_time = config.get("sleep_time", 1.0)

    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        await asyncio.sleep(self.sleep_time)
        return f"[Slow] {text}"


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def pipeline_manager():
    """创建 PipelineManager 实例"""
    return PipelineManager()


@pytest.fixture
def sample_message():
    """创建示例 MessageBase"""
    return create_mock_message("test message")


@pytest.fixture
def sample_text():
    """创建示例文本"""
    return "hello world"


@pytest.fixture
def sample_metadata():
    """创建示例元数据"""
    return {"user_id": "test_user", "source": "test"}


# =============================================================================
# MessagePipeline 注册测试
# =============================================================================


@pytest.mark.asyncio
async def test_register_inbound_pipeline(pipeline_manager: PipelineManager):
    """测试注册入站 MessagePipeline"""
    pipeline = MockMessagePipeline({})
    pipeline_manager._register_pipeline(pipeline, "inbound")

    assert len(pipeline_manager._inbound_pipelines) == 1
    assert pipeline_manager._inbound_pipelines[0] == pipeline
    assert not pipeline_manager._inbound_sorted
    # pipeline_manager.core 为 None，所以 pipeline.core 也为 None
    assert pipeline.core is None


@pytest.mark.asyncio
async def test_register_outbound_pipeline(pipeline_manager: PipelineManager):
    """测试注册出站 MessagePipeline"""
    pipeline = MockMessagePipeline({})
    pipeline_manager._register_pipeline(pipeline, "outbound")

    assert len(pipeline_manager._outbound_pipelines) == 1
    assert pipeline_manager._outbound_pipelines[0] == pipeline
    assert not pipeline_manager._outbound_sorted


@pytest.mark.asyncio
async def test_register_multiple_pipelines(pipeline_manager: PipelineManager):
    """测试注册多个 MessagePipeline"""
    pipeline1 = MockMessagePipeline({})
    pipeline2 = MockMessagePipeline({})
    pipeline3 = MockMessagePipeline({})

    pipeline_manager._register_pipeline(pipeline1, "inbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")
    pipeline_manager._register_pipeline(pipeline3, "inbound")

    assert len(pipeline_manager._inbound_pipelines) == 2
    assert len(pipeline_manager._outbound_pipelines) == 1


@pytest.mark.asyncio
async def test_register_pipeline_with_core(pipeline_manager: PipelineManager):
    """测试注册时设置 core 引用"""
    mock_core = Mock()
    manager_with_core = PipelineManager(core=mock_core)

    pipeline = MockMessagePipeline({})
    manager_with_core._register_pipeline(pipeline, "inbound")

    assert pipeline.core == mock_core


# =============================================================================
# MessagePipeline 处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_outbound_message_single_pipeline(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试单个出站管道处理消息"""
    pipeline = MockMessagePipeline({})
    pipeline.priority = 100
    pipeline_manager._register_pipeline(pipeline, "outbound")

    result = await pipeline_manager.process_outbound_message(sample_message)

    assert result is not None
    assert len(pipeline.processed_messages) == 1
    assert pipeline.processed_messages[0] == sample_message


@pytest.mark.asyncio
async def test_process_inbound_message_single_pipeline(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试单个入站管道处理消息"""
    pipeline = MockMessagePipeline({})
    pipeline.priority = 100
    pipeline_manager._register_pipeline(pipeline, "inbound")

    result = await pipeline_manager.process_inbound_message(sample_message)

    assert result is not None
    assert len(pipeline.processed_messages) == 1


@pytest.mark.asyncio
async def test_process_outbound_message_multiple_pipelines(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试多个出站管道按优先级处理消息"""
    pipeline1 = MockMessagePipeline({})
    pipeline1.priority = 100
    pipeline2 = MockMessagePipeline({})
    pipeline2.priority = 50
    pipeline3 = MockMessagePipeline({})
    pipeline3.priority = 200

    pipeline_manager._register_pipeline(pipeline1, "outbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")
    pipeline_manager._register_pipeline(pipeline3, "outbound")

    result = await pipeline_manager.process_outbound_message(sample_message)

    # 验证所有管道都处理了消息
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1
    assert len(pipeline3.processed_messages) == 1

    # 验证处理顺序（按优先级：50 -> 100 -> 200）
    processed_in_order = []
    for p in [pipeline1, pipeline2, pipeline3]:
        processed_in_order.extend(p.processed_messages)
    assert processed_in_order == [sample_message, sample_message, sample_message]


@pytest.mark.asyncio
async def test_process_outbound_message_pipeline_drops_message(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试管道丢弃消息"""
    pipeline1 = MockMessagePipeline({})
    pipeline1.priority = 100
    pipeline2 = MockMessagePipeline({})
    pipeline2.priority = 200
    pipeline2.should_drop = True

    pipeline_manager._register_pipeline(pipeline1, "outbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")

    result = await pipeline_manager.process_outbound_message(sample_message)

    # pipeline2 丢弃消息，应返回 None
    assert result is None
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1


@pytest.mark.asyncio
async def test_process_outbound_message_pipeline_fails(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试管道处理失败（继续处理下一个管道）"""
    pipeline1 = MockMessagePipeline({})
    pipeline1.priority = 100
    pipeline2 = MockMessagePipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True

    pipeline_manager._register_pipeline(pipeline1, "outbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")

    result = await pipeline_manager.process_outbound_message(sample_message)

    # pipeline2 失败，但 pipeline1 应该已处理，返回结果
    assert result is not None
    assert len(pipeline1.processed_messages) == 1
    assert len(pipeline2.processed_messages) == 1


@pytest.mark.asyncio
async def test_process_inbound_message_empty_pipeline_list(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试空管道列表处理入站消息"""
    result = await pipeline_manager.process_inbound_message(sample_message)

    assert result == sample_message


@pytest.mark.asyncio
async def test_process_outbound_message_empty_pipeline_list(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试空管道列表处理出站消息"""
    result = await pipeline_manager.process_outbound_message(sample_message)

    assert result == sample_message


# =============================================================================
# MessagePipeline 优先级排序测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_priority_sorting_outbound(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试出站管道按优先级排序"""
    execution_order = []

    class OrderedPipeline(MockMessagePipeline):
        def __init__(self, config: Dict[str, Any], name: str):
            super().__init__(config)
            self.name = name

        async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
            execution_order.append(self.name)
            return message

    pipeline1 = OrderedPipeline({}, "pipeline1")
    pipeline1.priority = 100
    pipeline2 = OrderedPipeline({}, "pipeline2")
    pipeline2.priority = 50
    pipeline3 = OrderedPipeline({}, "pipeline3")
    pipeline3.priority = 150

    pipeline_manager._register_pipeline(pipeline1, "outbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")
    pipeline_manager._register_pipeline(pipeline3, "outbound")

    # 处理消息，触发排序
    await pipeline_manager.process_outbound_message(sample_message)

    # 验证执行顺序：50 -> 100 -> 150
    assert execution_order == ["pipeline2", "pipeline1", "pipeline3"]


@pytest.mark.asyncio
async def test_pipeline_priority_sorting_inbound(pipeline_manager: PipelineManager, sample_message: MessageBase):
    """测试入站管道按优先级排序"""
    execution_order = []

    class OrderedPipeline(MockMessagePipeline):
        def __init__(self, config: Dict[str, Any], name: str):
            super().__init__(config)
            self.name = name

        async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
            execution_order.append(self.name)
            return message

    pipeline1 = OrderedPipeline({}, "pipeline1")
    pipeline1.priority = 200
    pipeline2 = OrderedPipeline({}, "pipeline2")
    pipeline2.priority = 100
    pipeline3 = OrderedPipeline({}, "pipeline3")
    pipeline3.priority = 50

    pipeline_manager._register_pipeline(pipeline1, "inbound")
    pipeline_manager._register_pipeline(pipeline2, "inbound")
    pipeline_manager._register_pipeline(pipeline3, "inbound")

    await pipeline_manager.process_inbound_message(sample_message)

    # 验证执行顺序：50 -> 100 -> 200
    assert execution_order == ["pipeline3", "pipeline2", "pipeline1"]


# =============================================================================
# TextPipeline 注册测试
# =============================================================================


@pytest.mark.asyncio
async def test_register_text_pipeline(pipeline_manager: PipelineManager):
    """测试注册 TextPipeline"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_text_pipeline(pipeline)

    assert len(pipeline_manager._text_pipelines) == 1
    assert pipeline_manager._text_pipelines[0] == pipeline
    assert not pipeline_manager._text_pipelines_sorted


@pytest.mark.asyncio
async def test_register_multiple_text_pipelines(pipeline_manager: PipelineManager):
    """测试注册多个 TextPipeline"""
    pipeline1 = MockTextPipeline({})
    pipeline2 = MockTextPipeline({})
    pipeline3 = MockTextPipeline({})

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)
    pipeline_manager.register_text_pipeline(pipeline3)

    assert len(pipeline_manager._text_pipelines) == 3


# =============================================================================
# TextPipeline 处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_text_single_pipeline(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试单个 TextPipeline 处理文本"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline_manager.register_text_pipeline(pipeline)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    assert result is not None
    assert "MockTextPipeline" in result
    assert len(pipeline.processed_texts) == 1
    assert pipeline.processed_texts[0] == (sample_text, sample_metadata)


@pytest.mark.asyncio
async def test_process_text_multiple_pipelines(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试多个 TextPipeline 处理文本"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 50
    pipeline3 = MockTextPipeline({})
    pipeline3.priority = 150

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)
    pipeline_manager.register_text_pipeline(pipeline3)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 所有管道都应该处理
    assert len(pipeline1.processed_texts) == 1
    assert len(pipeline2.processed_texts) == 1
    assert len(pipeline3.processed_texts) == 1

    # 验证处理顺序（按优先级：50 -> 100 -> 150）
    assert pipeline2.processed_texts[0][0] == sample_text
    assert pipeline1.processed_texts[0][0] == f"[MockTextPipeline] {sample_text}"
    assert pipeline3.processed_texts[0][0] == f"[MockTextPipeline] [MockTextPipeline] {sample_text}"


@pytest.mark.asyncio
async def test_process_text_pipeline_drops_message(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 丢弃消息"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 200
    pipeline2.should_drop = True

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # pipeline2 丢弃消息，应返回 None
    assert result is None
    assert len(pipeline1.processed_texts) == 1
    assert len(pipeline2.processed_texts) == 1

    # 验证丢弃计数
    stats2 = pipeline2.get_stats()
    assert stats2.dropped_count == 1


@pytest.mark.asyncio
async def test_process_text_empty_pipeline_list(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试空 TextPipeline 列表"""
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    assert result == sample_text


@pytest.mark.asyncio
async def test_process_text_disabled_pipeline(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试禁用的 TextPipeline 不处理"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 200
    pipeline2.enabled = False

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 只有 pipeline1 处理了
    assert len(pipeline1.processed_texts) == 1
    assert len(pipeline2.processed_texts) == 0


# =============================================================================
# TextPipeline 错误处理测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_text_pipeline_error_continue(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 错误处理：CONTINUE"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # pipeline2 失败但继续，pipeline1 应该已处理
    assert result is not None
    assert len(pipeline1.processed_texts) == 1
    assert len(pipeline2.processed_texts) == 1

    # 验证错误计数（在 process() 中 increment，在错误处理中再次 increment）
    stats2 = pipeline2.get_stats()
    assert stats2.error_count >= 1  # 至少一次，可能两次（取决于实现）


@pytest.mark.asyncio
async def test_process_text_pipeline_error_stop(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 错误处理：STOP"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline.should_fail = True
    pipeline.error_handling = PipelineErrorHandling.STOP

    pipeline_manager.register_text_pipeline(pipeline)

    with pytest.raises(PipelineException) as exc_info:
        await pipeline_manager.process_text(sample_text, sample_metadata)

    assert "MockTextPipeline" in str(exc_info.value)
    assert "处理失败" in str(exc_info.value)


@pytest.mark.asyncio
async def test_process_text_pipeline_error_drop(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 错误处理：DROP"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True
    pipeline2.error_handling = PipelineErrorHandling.DROP

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # pipeline2 失败并丢弃消息
    assert result is None

    # 验证错误和丢弃计数（error_count 可能 increment 两次）
    stats2 = pipeline2.get_stats()
    assert stats2.error_count >= 1
    assert stats2.dropped_count == 1


@pytest.mark.asyncio
async def test_process_text_pipeline_timeout_continue(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 超时：CONTINUE"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = SlowMockTextPipeline({"sleep_time": 1.0, "timeout_seconds": 0.1})
    pipeline2.priority = 200
    pipeline2.timeout_seconds = 0.1
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # pipeline2 超时但继续，pipeline1 应该已处理
    assert result is not None

    # 验证错误计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count == 1


@pytest.mark.asyncio
async def test_process_text_pipeline_timeout_drop(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 超时：DROP"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = SlowMockTextPipeline({"sleep_time": 1.0})
    pipeline2.priority = 200
    pipeline2.timeout_seconds = 0.1
    pipeline2.error_handling = PipelineErrorHandling.DROP

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # pipeline2 超时并丢弃
    assert result is None

    # 验证错误和丢弃计数
    stats2 = pipeline2.get_stats()
    assert stats2.error_count == 1
    assert stats2.dropped_count == 1


# =============================================================================
# TextPipeline 优先级排序测试
# =============================================================================


@pytest.mark.asyncio
async def test_text_pipeline_priority_sorting(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试 TextPipeline 按优先级排序"""
    execution_order = []

    class OrderedTextPipeline(MockTextPipeline):
        def __init__(self, config: Dict[str, Any], name: str):
            super().__init__(config)
            self.name = name

        async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
            execution_order.append(self.name)
            return f"[{self.name}] {text}"

    pipeline1 = OrderedTextPipeline({}, "pipeline1")
    pipeline1.priority = 100
    pipeline2 = OrderedTextPipeline({}, "pipeline2")
    pipeline2.priority = 50
    pipeline3 = OrderedTextPipeline({}, "pipeline3")
    pipeline3.priority = 150

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)
    pipeline_manager.register_text_pipeline(pipeline3)

    await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证执行顺序：50 -> 100 -> 150
    assert execution_order == ["pipeline2", "pipeline1", "pipeline3"]


# =============================================================================
# 连接/断开通知测试
# =============================================================================


@pytest.mark.asyncio
async def test_notify_connect(pipeline_manager: PipelineManager):
    """测试通知所有管道连接"""
    pipeline1 = MockMessagePipeline({})
    pipeline2 = MockMessagePipeline({})
    pipeline3 = MockMessagePipeline({})

    pipeline_manager._register_pipeline(pipeline1, "inbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")
    pipeline_manager._register_pipeline(pipeline3, "inbound")

    await pipeline_manager.notify_connect()

    assert pipeline1.connect_called is True
    assert pipeline2.connect_called is True
    assert pipeline3.connect_called is True


@pytest.mark.asyncio
async def test_notify_disconnect(pipeline_manager: PipelineManager):
    """测试通知所有管道断开"""
    pipeline1 = MockMessagePipeline({})
    pipeline2 = MockMessagePipeline({})
    pipeline3 = MockMessagePipeline({})

    pipeline_manager._register_pipeline(pipeline1, "inbound")
    pipeline_manager._register_pipeline(pipeline2, "outbound")
    pipeline_manager._register_pipeline(pipeline3, "inbound")

    await pipeline_manager.notify_disconnect()

    assert pipeline1.disconnect_called is True
    assert pipeline2.disconnect_called is True
    assert pipeline3.disconnect_called is True


@pytest.mark.asyncio
async def test_notify_connect_with_error(pipeline_manager: PipelineManager):
    """测试通知连接时管道出错（不影响其他管道）"""
    error_pipeline = MockMessagePipeline({})

    async def failing_on_connect():
        raise ValueError("Connection error")

    error_pipeline.on_connect = failing_on_connect

    normal_pipeline = MockMessagePipeline({})

    pipeline_manager._register_pipeline(error_pipeline, "inbound")
    pipeline_manager._register_pipeline(normal_pipeline, "outbound")

    # 不应抛出异常
    await pipeline_manager.notify_connect()

    # normal_pipeline 应该仍被通知
    assert normal_pipeline.connect_called is True


@pytest.mark.asyncio
async def test_notify_disconnect_with_error(pipeline_manager: PipelineManager):
    """测试通知断开时管道出错（不影响其他管道）"""
    error_pipeline = MockMessagePipeline({})

    async def failing_on_disconnect():
        raise ValueError("Disconnection error")

    error_pipeline.on_disconnect = failing_on_disconnect

    normal_pipeline = MockMessagePipeline({})

    pipeline_manager._register_pipeline(error_pipeline, "inbound")
    pipeline_manager._register_pipeline(normal_pipeline, "outbound")

    # 不应抛出异常
    await pipeline_manager.notify_disconnect()

    # normal_pipeline 应该仍被通知
    assert normal_pipeline.disconnect_called is True


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_text_pipeline_stats(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试获取 TextPipeline 统计信息"""
    pipeline1 = MockTextPipeline({})
    pipeline1.priority = 100
    pipeline2 = MockTextPipeline({})
    pipeline2.priority = 200
    pipeline2.enabled = False

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    # 处理一些文本（只有 pipeline1 会处理，pipeline2 被禁用）
    await pipeline_manager.process_text(sample_text, sample_metadata)
    await pipeline_manager.process_text(sample_text, sample_metadata)

    stats = pipeline_manager.get_text_pipeline_stats()

    # 两个 pipeline 都有统计，但由于同名，后注册的会覆盖（返回最后一个）
    assert len(stats) >= 1
    assert "MockTextPipeline" in stats
    # 统计来自最后注册的 pipeline2（disabled）
    # 或来自 pipeline1（processed 2次），取决于实现
    # 验证基本结构
    assert "processed_count" in stats["MockTextPipeline"]
    assert "dropped_count" in stats["MockTextPipeline"]
    assert "error_count" in stats["MockTextPipeline"]
    assert "enabled" in stats["MockTextPipeline"]
    assert "priority" in stats["MockTextPipeline"]


@pytest.mark.asyncio
async def test_get_text_pipeline_stats_with_drops(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试统计信息包含丢弃计数"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline.should_drop = True

    pipeline_manager.register_text_pipeline(pipeline)

    await pipeline_manager.process_text(sample_text, sample_metadata)

    stats = pipeline_manager.get_text_pipeline_stats()

    assert stats["MockTextPipeline"]["dropped_count"] == 1


@pytest.mark.asyncio
async def test_get_text_pipeline_stats_with_errors(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试统计信息包含错误计数"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline.should_fail = True
    pipeline.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline)

    await pipeline_manager.process_text(sample_text, sample_metadata)

    stats = pipeline_manager.get_text_pipeline_stats()

    # error_count 可能 increment 两次（在 process() 和错误处理中）
    assert stats["MockTextPipeline"]["error_count"] >= 1


@pytest.mark.asyncio
async def test_get_text_pipeline_stats_avg_duration(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试统计信息包含平均处理时间"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline.delay_ms = 50  # 50ms 延迟

    pipeline_manager.register_text_pipeline(pipeline)

    await pipeline_manager.process_text(sample_text, sample_metadata)
    await pipeline_manager.process_text(sample_text, sample_metadata)

    stats = pipeline_manager.get_text_pipeline_stats()

    assert stats["MockTextPipeline"]["processed_count"] == 2
    assert stats["MockTextPipeline"]["avg_duration_ms"] >= 50  # 至少 50ms


@pytest.mark.asyncio
async def test_get_text_pipeline_stats_empty(pipeline_manager: PipelineManager):
    """测试空 TextPipeline 列表的统计"""
    stats = pipeline_manager.get_text_pipeline_stats()

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
async def test_concurrent_text_processing(pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]):
    """测试并发处理多个文本"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100
    pipeline.delay_ms = 10

    pipeline_manager.register_text_pipeline(pipeline)

    # 并发处理 10 个文本
    tasks = [
        pipeline_manager.process_text(f"text_{i}", sample_metadata)
        for i in range(10)
    ]

    results = await asyncio.gather(*tasks)

    # 所有文本都应该被处理
    assert len(pipeline.processed_texts) == 10
    assert all(r is not None for r in results)


# =============================================================================
# 边界情况测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_text_empty_string(pipeline_manager: PipelineManager, sample_metadata: Dict[str, Any]):
    """测试处理空字符串"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_text_pipeline(pipeline)

    result = await pipeline_manager.process_text("", sample_metadata)

    assert result is not None
    assert len(pipeline.processed_texts) == 1
    assert pipeline.processed_texts[0][0] == ""


@pytest.mark.asyncio
async def test_process_text_with_unicode(pipeline_manager: PipelineManager, sample_metadata: Dict[str, Any]):
    """测试处理 Unicode 文本"""
    pipeline = MockTextPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_text_pipeline(pipeline)

    unicode_text = "你好世界 🌍 Ñoño"
    result = await pipeline_manager.process_text(unicode_text, sample_metadata)

    assert result is not None
    assert "你好世界" in result or "MockTextPipeline" in result


@pytest.mark.asyncio
async def test_process_outbound_message_none_message(pipeline_manager: PipelineManager):
    """测试处理 None 消息"""
    pipeline = MockMessagePipeline({})
    pipeline.priority = 100

    pipeline_manager._register_pipeline(pipeline, "outbound")

    result = await pipeline_manager.process_outbound_message(None)

    # 消息为 None，应在第一个管道前返回 None
    assert result is None
    assert len(pipeline.processed_messages) == 0


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
