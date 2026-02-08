"""
Pipeline 回滚功能测试（问题#4修复验证）

测试 PipelineContext 回滚机制，确保管道失败时可以正确恢复状态。

运行: uv run pytest tests/core/test_pipeline_rollback.py -v
"""

import pytest
from typing import Optional, Dict, Any

from src.domains.input.pipelines.manager import (
    PipelineManager,
    TextPipelineBase,
    PipelineErrorHandling,
    PipelineContext,
)


# =============================================================================
# Mock Pipeline 实现（支持回滚）
# =============================================================================


class StatefulTextPipeline(TextPipelineBase):
    """
    有状态的 TextPipeline，用于测试回滚功能

    问题#4修复：通过 context 注册回滚动作
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.state_changes = []  # 记录状态变化
        self.should_fail = False

    async def _process(
        self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        """处理文本并记录状态变化"""

        # 模拟状态修改
        state_id = f"state_{len(self.state_changes)}"
        self.state_changes.append(state_id)

        # 注册回滚动作
        if context is not None:

            def rollback():
                # 移除最后的状态变化
                if self.state_changes and self.state_changes[-1] == state_id:
                    self.state_changes.pop()

            context.add_rollback(rollback)

        # 模拟失败
        if self.should_fail:
            raise ValueError(f"Pipeline {self.__class__.__name__} failed")

        return f"[{state_id}] {text}"


class ModifyingTextPipeline(TextPipelineBase):
    """
    修改文本的 TextPipeline，用于测试 CONTINUE 模式下的回滚

    问题#4修复：CONTINUE 模式下失败应该恢复到原始文本
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.prefix = config.get("prefix", "MODIFIED")
        self.should_fail = False

    async def _process(
        self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        """修改文本"""

        # 模拟失败
        if self.should_fail:
            raise ValueError(f"Pipeline {self.__class__.__name__} failed")

        # 修改文本
        return f"{self.prefix}: {text}"


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def pipeline_manager():
    """创建 PipelineManager 实例"""
    return PipelineManager()


@pytest.fixture
def sample_text():
    """创建示例文本"""
    return "hello world"


@pytest.fixture
def sample_metadata():
    """创建示例元数据"""
    return {"user_id": "test_user", "source": "test"}


# =============================================================================
# 回滚功能测试
# =============================================================================


@pytest.mark.asyncio
async def test_pipeline_rollback_on_continue_error(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试 CONTINUE 模式下管道失败时回滚所有副作用并使用原始文本继续"""
    # 注意：CONTINUE 模式下，当某个管道失败时：
    # 1. 回滚所有已注册的回滚动作（包括之前成功管道的副作用）
    # 2. 使用原始文本继续执行后续管道

    pipeline1 = StatefulTextPipeline({})
    pipeline1.priority = 100
    pipeline1.error_handling = PipelineErrorHandling.CONTINUE

    pipeline2 = ModifyingTextPipeline({"prefix": "MODIFIED"})
    pipeline2.priority = 200
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE
    pipeline2.should_fail = True  # 会在处理时失败

    pipeline3 = StatefulTextPipeline({})
    pipeline3.priority = 300
    pipeline3.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)
    pipeline_manager.register_text_pipeline(pipeline3)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证结果：
    # 1. pipeline1 成功时添加了状态，但 pipeline2 失败后被回滚
    assert len(pipeline1.state_changes) == 0  # 被回滚了

    # 2. pipeline3 收到原始文本（因为 pipeline2 失败后回滚到原始状态）
    assert len(pipeline3.state_changes) == 1

    # 3. 最终结果是 pipeline3 处理原始文本后的结果
    assert result == "[state_0] hello world"


@pytest.mark.asyncio
async def test_pipeline_rollback_on_drop_error(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试 DROP 模式下管道失败时回滚所有状态"""
    pipeline1 = StatefulTextPipeline({})
    pipeline1.priority = 100

    pipeline2 = StatefulTextPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True  # 会在处理时失败
    pipeline2.error_handling = PipelineErrorHandling.DROP

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证结果：
    # 1. pipeline2 失败，DROP 模式返回 None
    assert result is None

    # 2. DROP 模式应该回滚所有副作用，包括 pipeline1 的状态
    assert len(pipeline1.state_changes) == 0


@pytest.mark.asyncio
async def test_pipeline_rollback_on_stop_error(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试 STOP 模式下管道失败时回滚所有状态并抛出异常"""
    pipeline1 = StatefulTextPipeline({})
    pipeline1.priority = 100

    pipeline2 = StatefulTextPipeline({})
    pipeline2.priority = 200
    pipeline2.should_fail = True  # 会在处理时失败
    pipeline2.error_handling = PipelineErrorHandling.STOP

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)

    # 执行处理，应该抛出异常
    with pytest.raises(Exception, match="Pipeline .* failed"):
        await pipeline_manager.process_text(sample_text, sample_metadata)

    # STOP 模式应该回滚所有副作用，包括 pipeline1 的状态
    assert len(pipeline1.state_changes) == 0


@pytest.mark.asyncio
async def test_pipeline_rollback_on_normal_drop(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试管道正常返回 None 时回滚所有状态"""
    pipeline1 = StatefulTextPipeline({})
    pipeline1.priority = 100

    pipeline2 = StatefulTextPipeline({})
    pipeline2.priority = 200
    # 模拟管道返回 None（例如过滤）

    # 创建一个会返回 None 的管道
    class DropTextPipeline(TextPipelineBase):
        async def _process(
            self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
        ) -> Optional[str]:
            return None  # 丢弃消息

    drop_pipeline = DropTextPipeline({})
    drop_pipeline.priority = 200

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(drop_pipeline)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证结果：管道返回 None，回滚所有状态
    assert result is None
    assert len(pipeline1.state_changes) == 0


@pytest.mark.asyncio
async def test_pipeline_context_multiple_rollback_actions(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试单个管道注册多个回滚动作"""

    class MultiRollbackPipeline(TextPipelineBase):
        def __init__(self, config: Dict[str, Any]):
            super().__init__(config)
            self.state = []

        async def _process(
            self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
        ) -> Optional[str]:
            if context is not None:
                # 注册多个回滚动作
                for i in range(3):
                    state_id = f"state_{i}"
                    self.state.append(state_id)

                    def rollback(sid=state_id):
                        if sid in self.state:
                            self.state.remove(sid)

                    context.add_rollback(rollback)

            # 模拟失败
            raise ValueError("Test failure")

    pipeline = MultiRollbackPipeline({})
    pipeline.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证：所有回滚动作都应该被执行
    assert len(pipeline.state) == 0
    # CONTINUE 模式返回原始文本
    assert result == sample_text


@pytest.mark.asyncio
async def test_pipeline_rollback_preserves_original_text(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试 CONTINUE 模式下失败后使用原始文本继续"""
    pipeline1 = ModifyingTextPipeline({"prefix": "FIRST"})
    pipeline1.priority = 100

    pipeline2 = ModifyingTextPipeline({"prefix": "SECOND"})
    pipeline2.priority = 200
    pipeline2.should_fail = True  # 会在处理时失败
    pipeline2.error_handling = PipelineErrorHandling.CONTINUE

    pipeline3 = ModifyingTextPipeline({"prefix": "THIRD"})
    pipeline3.priority = 300

    pipeline_manager.register_text_pipeline(pipeline1)
    pipeline_manager.register_text_pipeline(pipeline2)
    pipeline_manager.register_text_pipeline(pipeline3)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证：
    # pipeline1 成功
    # pipeline2 失败，回滚并使用原始文本
    # pipeline3 收到原始文本，不是 pipeline1 修改后的文本
    assert result == "THIRD: hello world"
    # 而不是 "THIRD: FIRST: hello world"


@pytest.mark.asyncio
async def test_pipeline_no_rollback_on_success(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试管道成功时不执行回滚"""
    pipeline = StatefulTextPipeline({})
    pipeline.priority = 100

    pipeline_manager.register_text_pipeline(pipeline)

    # 执行处理
    result = await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证：管道成功，状态应该被保留
    assert len(pipeline.state_changes) == 1
    assert result == "[state_0] hello world"


@pytest.mark.asyncio
async def test_pipeline_rollback_execution_order(
    pipeline_manager: PipelineManager, sample_text: str, sample_metadata: Dict[str, Any]
):
    """测试回滚动作按逆序执行"""
    order = []

    class OrderedRollbackPipeline(TextPipelineBase):
        async def _process(
            self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
        ) -> Optional[str]:
            if context is not None:
                # 添加多个回滚动作
                for i in range(3):

                    def rollback(idx=i):
                        order.append(f"rollback_{idx}")

                    context.add_rollback(rollback)

            raise ValueError("Test failure")

    pipeline = OrderedRollbackPipeline({})
    pipeline.error_handling = PipelineErrorHandling.CONTINUE

    pipeline_manager.register_text_pipeline(pipeline)

    # 执行处理
    await pipeline_manager.process_text(sample_text, sample_metadata)

    # 验证：回滚动作应该按逆序执行（2, 1, 0）
    assert order == ["rollback_2", "rollback_1", "rollback_0"]
