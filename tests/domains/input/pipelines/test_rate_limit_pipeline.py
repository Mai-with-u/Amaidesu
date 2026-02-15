"""
测试 RateLimitInputPipeline (限流管道)

运行: uv run pytest tests/domains/input/pipelines/test_rate_limit_pipeline.py -v
"""

import asyncio

import pytest

from src.domains.input.pipelines.rate_limit.pipeline import RateLimitInputPipeline
from src.modules.types.base.normalized_message import NormalizedMessage

# =============================================================================
# Fixtures
# =============================================================================


class MockRawMessage:
    """Mock 原始消息对象，用于测试 user_id 属性"""

    def __init__(self, user_id: str, user_name: str = "test_user"):
        self.open_id = user_id
        self.uname = user_name

    def get_user_id(self) -> str:
        return self.open_id

    def get_display_text(self) -> str:
        return f"[{self.uname}]"

    def model_dump(self) -> dict:
        return {"open_id": self.open_id, "uname": self.uname}


@pytest.fixture
def rate_limit_pipeline():
    """创建限流管道实例"""
    config = {
        "global_rate_limit": 10,
        "user_rate_limit": 3,
        "window_size": 60,
    }
    return RateLimitInputPipeline(config)


def create_message(text: str, user_id: str = "test_user", group_id: str = "test_group") -> NormalizedMessage:
    """创建测试用的 NormalizedMessage"""
    raw = MockRawMessage(user_id=user_id)
    return NormalizedMessage(
        text=text,
        source="test",
        raw=raw,
        metadata={"group_id": group_id},
    )


# =============================================================================
# 创建和初始化测试
# =============================================================================


def test_rate_limit_pipeline_creation(rate_limit_pipeline):
    """测试管道创建"""
    assert rate_limit_pipeline is not None
    assert rate_limit_pipeline._global_rate_limit == 10
    assert rate_limit_pipeline._user_rate_limit == 3
    assert rate_limit_pipeline._window_size == 60
    assert rate_limit_pipeline.priority == 100


def test_rate_limit_pipeline_custom_config():
    """测试自定义配置"""
    config = {
        "global_rate_limit": 100,
        "user_rate_limit": 10,
        "window_size": 30,
    }
    pipeline = RateLimitInputPipeline(config)
    assert pipeline._global_rate_limit == 100
    assert pipeline._user_rate_limit == 10
    assert pipeline._window_size == 30


# =============================================================================
# _process() 方法测试
# =============================================================================


@pytest.mark.asyncio
async def test_process_message_pass_through(rate_limit_pipeline):
    """测试消息通过限流"""
    message = create_message("测试消息")
    result = await rate_limit_pipeline._process(message)
    assert result == message


@pytest.mark.asyncio
async def test_process_message_global_limit():
    """测试全局限流"""
    # 创建低限制的管道
    config = {"global_rate_limit": 2, "user_rate_limit": 10, "window_size": 60}
    pipeline = RateLimitInputPipeline(config)

    # 前两条消息应该通过
    result1 = await pipeline._process(create_message("消息1", "user1"))
    assert result1 is not None

    result2 = await pipeline._process(create_message("消息2", "user1"))
    assert result2 is not None

    # 第三条消息应该被限流
    result3 = await pipeline._process(create_message("消息3", "user1"))
    assert result3 is None


@pytest.mark.asyncio
async def test_process_message_user_limit(rate_limit_pipeline):
    """测试用户级别限流"""
    # 发送3条消息（用户限制）
    for i in range(3):
        result = await rate_limit_pipeline._process(create_message(f"消息{i}", "user1"))
        assert result is not None

    # 第4条消息应该被限流
    result = await rate_limit_pipeline._process(create_message("消息4", "user1"))
    assert result is None


@pytest.mark.asyncio
async def test_process_message_different_users(rate_limit_pipeline):
    """测试不同用户的独立限流"""
    # 用户1发送3条消息
    for i in range(3):
        result = await rate_limit_pipeline._process(create_message(f"用户1消息{i}", "user1"))
        assert result is not None

    # 用户1的第4条消息应该被限流
    result = await rate_limit_pipeline._process(create_message("用户1消息3", "user1"))
    assert result is None

    # 用户2应该还能发送消息（独立计数）
    result = await rate_limit_pipeline._process(create_message("用户2消息0", "user2"))
    assert result is not None


# =============================================================================
# 时间窗口计算测试
# =============================================================================


@pytest.mark.asyncio
async def test_window_cleanup(rate_limit_pipeline):
    """测试重置管道清空所有记录"""
    # 用户限制是3，所以只能通过3条消息
    for i in range(3):
        await rate_limit_pipeline.process(create_message(f"消息{i}", "user1"))

    # 记录当前时间戳数量
    count_before = len(rate_limit_pipeline._global_timestamps)
    assert count_before == 3  # 只有3条通过

    # 重置管道（清空所有记录）
    await rate_limit_pipeline.reset()

    # 验证所有记录都被清空
    assert len(rate_limit_pipeline._global_timestamps) == 0
    assert len(rate_limit_pipeline._user_timestamps) == 0


@pytest.mark.asyncio
async def test_window_reset(rate_limit_pipeline):
    """测试管道重置"""
    # 添加一些消息
    for i in range(5):
        await rate_limit_pipeline._process(create_message(f"消息{i}", "user1"))

    # 重置
    await rate_limit_pipeline.reset()

    # 验证状态已清空
    assert len(rate_limit_pipeline._global_timestamps) == 0
    assert len(rate_limit_pipeline._user_timestamps) == 0
    assert rate_limit_pipeline._stats.processed_count == 0


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_info(rate_limit_pipeline):
    """测试获取管道信息"""
    info = rate_limit_pipeline.get_info()

    assert info["global_rate_limit"] == 10
    assert info["user_rate_limit"] == 3
    assert info["window_size"] == 60
    assert "current_global_count" in info
    assert "active_users" in info


@pytest.mark.asyncio
async def test_statistics_tracking(rate_limit_pipeline):
    """测试统计信息跟踪"""
    # 重置统计信息确保测试从干净状态开始
    rate_limit_pipeline.reset_stats()

    # 使用 process() 而不是 _process()，确保统计信息被正确记录
    await rate_limit_pipeline.process(create_message("消息1", "user1"))
    await rate_limit_pipeline.process(create_message("消息2", "user1"))

    stats = rate_limit_pipeline.get_stats()
    assert stats.processed_count >= 2

    # 发送被限流的消息
    for i in range(10):
        await rate_limit_pipeline.process(create_message(f"消息{i}", "user1"))

    stats = rate_limit_pipeline.get_stats()
    # 用户限制是3，前3条通过，后续被限流
    # 注意：dropped_count 是在管道内部手动增加的，不是通过异常
    assert stats.dropped_count > 0


# =============================================================================
# 边界条件测试
# =============================================================================


@pytest.mark.asyncio
async def test_empty_text(rate_limit_pipeline):
    """测试空文本处理"""
    message = create_message("")
    result = await rate_limit_pipeline._process(message)
    # 空文本应该通过（限流不关心内容）
    assert result is not None
    assert result.text == ""


@pytest.mark.asyncio
async def test_missing_user_id(rate_limit_pipeline):
    """测试缺少 user_id 的消息（raw 为 None）"""
    message = NormalizedMessage(text="测试消息", source="test", raw=None)

    # 应该使用默认值 "unknown_user"
    result = await rate_limit_pipeline._process(message)
    assert result is not None


@pytest.mark.asyncio
async def test_concurrent_access(rate_limit_pipeline):
    """测试并发访问"""
    # 并发发送多条消息
    tasks = []
    for i in range(10):
        task = rate_limit_pipeline._process(create_message(f"消息{i}", "user1"))
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # 验证部分消息通过，部分被限流
    passed = sum(1 for r in results if r is not None)
    assert passed > 0  # 至少有一些通过
    assert passed < 10  # 不是全部通过（有限流）


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
