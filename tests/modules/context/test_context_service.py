"""
ContextService 单元测试

测试对话上下文服务的所有核心功能：
- 初始化和清理
- 消息添加和获取
- 多会话隔离
- 上下文构建
- 会话管理（清空、删除、列出）
- 统计信息
- 边界测试（消息数/会话数限制）

运行: uv run pytest tests/services/context/test_context_service.py -v
"""

import time

import pytest

from src.modules.context import (
    ContextService,
    ContextServiceConfig,
    ConversationMessage,
    MessageRole,
    SessionInfo,  # noqa: F401 - Part of public API, used for documentation
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
async def context_service():
    """创建 ContextService 实例（默认配置）"""
    service = ContextService()
    await service.initialize()
    yield service
    await service.cleanup()


@pytest.fixture
async def limited_context_service():
    """创建有限制的 ContextService 实例（用于边界测试）"""
    config = ContextServiceConfig(
        max_messages_per_session=3,
        max_sessions=2,
    )
    service = ContextService(config=config)
    await service.initialize()
    yield service
    await service.cleanup()


# =============================================================================
# 初始化和清理测试
# =============================================================================


@pytest.mark.asyncio
async def test_initialize(context_service: ContextService):
    """测试初始化"""
    assert context_service._initialized is True


@pytest.mark.asyncio
async def test_initialize_idempotent(context_service: ContextService):
    """测试重复初始化不会出错"""
    # 第一次初始化已在 fixture 中完成
    assert context_service._initialized is True

    # 再次调用 initialize 应该安全返回
    await context_service.initialize()
    assert context_service._initialized is True


@pytest.mark.asyncio
async def test_cleanup(context_service: ContextService):
    """测试清理"""
    # 添加一些数据
    await context_service.add_message("test_session", MessageRole.USER, "Hello")

    # 清理
    await context_service.cleanup()

    assert context_service._initialized is False


@pytest.mark.asyncio
async def test_uninitialized_service_raises_error():
    """测试未初始化服务抛出错误"""
    service = ContextService()
    # 不要调用 initialize()

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.add_message("test", MessageRole.USER, "Hello")

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.get_history("test")

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.build_context("test")

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.get_session_info("test")

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.list_sessions()

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.clear_session("test")

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.delete_session("test")


# =============================================================================
# 消息添加和获取测试
# =============================================================================


@pytest.mark.asyncio
async def test_add_and_get_message(context_service: ContextService):
    """测试添加和获取消息"""
    session_id = "test_session"
    message = await context_service.add_message(
        session_id=session_id,
        role=MessageRole.USER,
        content="Hello, world!",
    )

    # 验证返回的消息对象
    assert isinstance(message, ConversationMessage)
    assert message.session_id == session_id
    assert message.role == MessageRole.USER
    assert message.content == "Hello, world!"
    assert message.message_id is not None

    # 获取历史
    history = await context_service.get_history(session_id)
    assert len(history) == 1
    assert history[0].content == "Hello, world!"
    assert history[0].role == MessageRole.USER


@pytest.mark.asyncio
async def test_get_history_empty_session(context_service: ContextService):
    """测试获取空会话的历史"""
    history = await context_service.get_history("nonexistent_session")
    assert history == []


@pytest.mark.asyncio
async def test_get_history_order(context_service: ContextService):
    """测试获取历史的顺序（时间正序）"""
    session_id = "test_session"

    # 添加多条消息
    await context_service.add_message(session_id, MessageRole.USER, "Message 1")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "Response 1")
    await context_service.add_message(session_id, MessageRole.USER, "Message 2")

    history = await context_service.get_history(session_id)

    # 验证顺序
    assert len(history) == 3
    assert history[0].content == "Message 1"
    assert history[1].content == "Response 1"
    assert history[2].content == "Message 2"


# =============================================================================
# 多会话隔离测试
# =============================================================================


@pytest.mark.asyncio
async def test_multi_session_isolation(context_service: ContextService):
    """测试多会话隔离"""
    await context_service.add_message("session1", MessageRole.USER, "Message 1")
    await context_service.add_message("session2", MessageRole.USER, "Message 2")
    await context_service.add_message("session1", MessageRole.ASSISTANT, "Response 1")

    history1 = await context_service.get_history("session1")
    history2 = await context_service.get_history("session2")

    # session1 应该有2条消息
    assert len(history1) == 2
    assert history1[0].content == "Message 1"
    assert history1[1].content == "Response 1"

    # session2 应该有1条消息
    assert len(history2) == 1
    assert history2[0].content == "Message 2"


@pytest.mark.asyncio
async def test_session_info_independent(context_service: ContextService):
    """测试不同会话的信息独立"""
    await context_service.add_message("session1", MessageRole.USER, "M1")
    await context_service.add_message("session1", MessageRole.USER, "M2")
    await context_service.add_message("session2", MessageRole.USER, "M3")

    info1 = await context_service.get_session_info("session1")
    info2 = await context_service.get_session_info("session2")

    assert info1.message_count == 2
    assert info2.message_count == 1


# =============================================================================
# 上下文构建测试
# =============================================================================


@pytest.mark.asyncio
async def test_build_context(context_service: ContextService):
    """测试构建 LLM 上下文"""
    session_id = "test_session"
    await context_service.add_message(session_id, MessageRole.SYSTEM, "You are helpful.")
    await context_service.add_message(session_id, MessageRole.USER, "Hello!")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "Hi there!")

    context = await context_service.build_context(session_id)

    assert len(context) == 3
    assert context[0]["role"] == "system"
    assert context[0]["content"] == "You are helpful."
    assert context[1]["role"] == "user"
    assert context[1]["content"] == "Hello!"
    assert context[2]["role"] == "assistant"
    assert context[2]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_build_context_exclude_system_prompt(context_service: ContextService):
    """测试构建上下文时排除系统提示"""
    session_id = "test_session"
    await context_service.add_message(session_id, MessageRole.SYSTEM, "You are helpful.")
    await context_service.add_message(session_id, MessageRole.USER, "Hello!")

    # 不包含系统提示
    context = await context_service.build_context(session_id, include_system_prompt=False)

    assert len(context) == 1
    assert context[0]["role"] == "user"
    assert context[0]["content"] == "Hello!"


@pytest.mark.asyncio
async def test_build_context_empty_session(context_service: ContextService):
    """测试为空会话构建上下文"""
    context = await context_service.build_context("nonexistent_session")
    assert context == []


# =============================================================================
# 会话管理测试
# =============================================================================


@pytest.mark.asyncio
async def test_clear_session(context_service: ContextService):
    """测试清空会话"""
    session_id = "test_session"
    await context_service.add_message(session_id, MessageRole.USER, "Message 1")
    await context_service.add_message(session_id, MessageRole.USER, "Message 2")

    # 验证消息存在
    history = await context_service.get_history(session_id)
    assert len(history) == 2

    # 清空会话
    await context_service.clear_session(session_id)

    # 验证消息已清空
    history = await context_service.get_history(session_id)
    assert len(history) == 0

    # 验证会话信息仍然存在（但消息数为0）
    session_info = await context_service.get_session_info(session_id)
    assert session_info is not None
    assert session_info.message_count == 0


@pytest.mark.asyncio
async def test_delete_session(context_service: ContextService):
    """测试删除会话"""
    session_id = "test_session"
    await context_service.add_message(session_id, MessageRole.USER, "Message 1")

    # 验证会话存在
    session_info = await context_service.get_session_info(session_id)
    assert session_info is not None

    # 删除会话
    await context_service.delete_session(session_id)

    # 验证会话已删除
    session_info = await context_service.get_session_info(session_id)
    assert session_info is None

    # 验证历史也已清空
    history = await context_service.get_history(session_id)
    assert history == []


@pytest.mark.asyncio
async def test_list_sessions(context_service: ContextService):
    """测试列出会话"""
    await context_service.add_message("session1", MessageRole.USER, "Message 1")
    await context_service.add_message("session2", MessageRole.USER, "Message 2")
    await context_service.add_message("session3", MessageRole.USER, "Message 3")

    sessions = await context_service.list_sessions()

    assert len(sessions) == 3
    session_ids = {s.session_id for s in sessions}
    assert "session1" in session_ids
    assert "session2" in session_ids
    assert "session3" in session_ids


@pytest.mark.asyncio
async def test_list_sessions_sorted_by_last_active(context_service: ContextService):
    """测试会话列表按最后活跃时间倒序排列"""
    # 创建会话并添加消息，有时间间隔
    await context_service.add_message("session1", MessageRole.USER, "M1")
    time.sleep(0.01)  # 确保时间戳不同
    await context_service.add_message("session2", MessageRole.USER, "M2")
    time.sleep(0.01)
    await context_service.add_message("session3", MessageRole.USER, "M3")

    # 再次活跃 session1
    time.sleep(0.01)
    await context_service.add_message("session1", MessageRole.USER, "M1-2")

    sessions = await context_service.list_sessions()

    # session1 应该排在最前面（最后活跃）
    assert sessions[0].session_id == "session1"
    # session2 和 session3 的相对顺序
    assert sessions[1].session_id in {"session2", "session3"}
    assert sessions[2].session_id in {"session2", "session3"}


@pytest.mark.asyncio
async def test_list_sessions_with_limit(context_service: ContextService):
    """测试限制返回的会话数量"""
    await context_service.add_message("session1", MessageRole.USER, "M1")
    await context_service.add_message("session2", MessageRole.USER, "M2")
    await context_service.add_message("session3", MessageRole.USER, "M3")

    sessions = await context_service.list_sessions(limit=2)

    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_list_sessions_active_only(context_service: ContextService):
    """测试只返回活跃会话"""
    # 创建一个活跃会话
    await context_service.add_message("active_session", MessageRole.USER, "M1")

    # 创建一个旧会话（通过修改 last_active 模拟）
    # 由于我们使用 MemoryStorage，这里只测试功能存在
    # 实际的超时行为需要更复杂的测试

    sessions = await context_service.list_sessions(active_only=True)

    # 由于刚创建的会话都在1小时内，应该都能查到
    assert len(sessions) >= 1
    assert any(s.session_id == "active_session" for s in sessions)


# =============================================================================
# 会话信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_session_info(context_service: ContextService):
    """测试获取会话信息"""
    session_id = "test_session"
    await context_service.add_message(session_id, MessageRole.USER, "Message 1")
    await context_service.add_message(session_id, MessageRole.USER, "Message 2")

    session_info = await context_service.get_session_info(session_id)

    assert session_info is not None
    assert session_info.session_id == session_id
    assert session_info.message_count == 2
    assert session_info.created_at > 0
    assert session_info.last_active > 0


@pytest.mark.asyncio
async def test_get_session_info_nonexistent(context_service: ContextService):
    """测试获取不存在的会话信息"""
    session_info = await context_service.get_session_info("nonexistent_session")
    assert session_info is None


@pytest.mark.asyncio
async def test_session_info_updates_on_add_message(context_service: ContextService):
    """测试添加消息后会话信息更新"""
    session_id = "test_session"

    # 初始状态
    await context_service.add_message(session_id, MessageRole.USER, "M1")
    info1 = await context_service.get_session_info(session_id)
    assert info1.message_count == 1

    # 添加更多消息
    await context_service.add_message(session_id, MessageRole.USER, "M2")
    await context_service.add_message(session_id, MessageRole.USER, "M3")

    info2 = await context_service.get_session_info(session_id)
    assert info2.message_count == 3
    assert info2.last_active >= info1.last_active


# =============================================================================
# 统计信息测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_statistics(context_service: ContextService):
    """测试获取统计信息"""
    stats = context_service.get_statistics()

    assert isinstance(stats, dict)
    assert "initialized" in stats
    assert "storage_type" in stats
    assert "max_messages_per_session" in stats
    assert "max_sessions" in stats
    assert "session_timeout_seconds" in stats
    assert "enable_persistence" in stats

    # 验证值
    assert stats["initialized"] is True
    assert stats["storage_type"] == "memory"
    assert stats["max_messages_per_session"] > 0
    assert stats["max_sessions"] > 0


@pytest.mark.asyncio
async def test_get_statistics_with_custom_config():
    """测试自定义配置的统计信息"""
    config = ContextServiceConfig(
        max_messages_per_session=50,
        max_sessions=20,
        session_timeout_seconds=1800,
        enable_persistence=True,
    )
    service = ContextService(config=config)
    await service.initialize()

    stats = service.get_statistics()

    assert stats["max_messages_per_session"] == 50
    assert stats["max_sessions"] == 20
    assert stats["session_timeout_seconds"] == 1800
    assert stats["enable_persistence"] is True

    await service.cleanup()


@pytest.mark.asyncio
async def test_get_statistics_uninitialized():
    """测试未初始化服务的统计信息"""
    service = ContextService()
    stats = service.get_statistics()

    assert stats["initialized"] is False


# =============================================================================
# 历史查询高级功能测试
# =============================================================================


@pytest.mark.asyncio
async def test_get_history_with_limit(context_service: ContextService):
    """测试获取历史时的数量限制"""
    session_id = "test_session"

    # 添加10条消息
    for i in range(10):
        await context_service.add_message(session_id, MessageRole.USER, f"Message {i}")

    # 获取最后5条
    history = await context_service.get_history(session_id, limit=5)
    assert len(history) == 5
    assert history[0].content == "Message 5"
    assert history[4].content == "Message 9"


@pytest.mark.asyncio
async def test_get_history_with_timestamp(context_service: ContextService):
    """测试按时间戳获取历史"""
    session_id = "test_session"

    # 添加第一条消息
    msg1 = await context_service.add_message(session_id, MessageRole.USER, "Message 1")

    # 使用第一条消息的时间戳作为分界点
    timestamp = msg1.timestamp
    time.sleep(0.01)  # 确保时间戳不同

    # 添加更多消息
    await context_service.add_message(session_id, MessageRole.USER, "Message 2")
    await context_service.add_message(session_id, MessageRole.USER, "Message 3")

    # 获取指定时间之前的消息（由于使用 < 比较，应该排除 timestamp 对应的消息）
    # 这意味着只会获取严格小于 timestamp 的消息
    history = await context_service.get_history(session_id, before_timestamp=timestamp)
    # 由于 Message 1 的 timestamp 等于过滤值，使用 < 比较会被排除
    # 因此这里应该期望 0 条消息
    assert len(history) == 0


@pytest.mark.asyncio
async def test_get_history_with_limit_and_timestamp(context_service: ContextService):
    """测试同时使用 limit 和 before_timestamp"""
    session_id = "test_session"

    # 添加消息，确保每条消息有不同的时间戳
    messages = []
    for i in range(5):
        msg = await context_service.add_message(session_id, MessageRole.USER, f"Message {i}")
        messages.append(msg)
        if i < 4:  # 最后一条不需要等待
            time.sleep(0.01)

    # 使用第3条消息的时间戳作为分界点
    # Message 0, 1, 2 应该在之前，Message 3, 4 在之后
    timestamp = messages[2].timestamp
    time.sleep(0.01)

    # 再添加一些消息
    for i in range(5, 7):
        await context_service.add_message(session_id, MessageRole.USER, f"Message {i}")

    # 获取 timestamp 之前的最后3条消息
    # 应该是 Message 0, 1, 2（但由于 < 比较和相同时间戳问题，实际可能会有所不同）
    history = await context_service.get_history(session_id, limit=3, before_timestamp=timestamp)

    # 验证有消息返回，且时间戳都小于过滤值
    assert len(history) >= 2
    for msg in history:
        assert msg.timestamp < timestamp


# =============================================================================
# 边界测试
# =============================================================================


@pytest.mark.asyncio
async def test_max_messages_per_session_limit(limited_context_service: ContextService):
    """测试达到每会话最大消息数时的行为"""
    session_id = "test_session"

    # 添加超过限制的消息（限制为3）
    await limited_context_service.add_message(session_id, MessageRole.USER, "Message 1")
    await limited_context_service.add_message(session_id, MessageRole.USER, "Message 2")
    await limited_context_service.add_message(session_id, MessageRole.USER, "Message 3")
    await limited_context_service.add_message(session_id, MessageRole.USER, "Message 4")
    await limited_context_service.add_message(session_id, MessageRole.USER, "Message 5")

    # 应该只保留最后3条消息（FIFO）
    history = await limited_context_service.get_history(session_id)
    assert len(history) == 3
    assert history[0].content == "Message 3"
    assert history[1].content == "Message 4"
    assert history[2].content == "Message 5"


@pytest.mark.asyncio
async def test_max_messages_per_session_message_order_preserved(limited_context_service: ContextService):
    """测试达到消息数限制时保持消息顺序"""
    session_id = "test_session"

    # 添加多条消息
    for i in range(10):
        await limited_context_service.add_message(session_id, MessageRole.USER, f"M{i}")

    # 验证最后3条消息的顺序正确
    history = await limited_context_service.get_history(session_id)
    assert len(history) == 3
    assert history[0].content == "M7"
    assert history[1].content == "M8"
    assert history[2].content == "M9"


@pytest.mark.asyncio
async def test_max_sessions_limit(limited_context_service: ContextService):
    """测试达到最大会话数时的行为"""
    # 添加2个会话（达到限制）
    await limited_context_service.add_message("session1", MessageRole.USER, "M1")
    await limited_context_service.add_message("session2", MessageRole.USER, "M2")

    # 验证两个会话都存在
    info1 = await limited_context_service.get_session_info("session1")
    info2 = await limited_context_service.get_session_info("session2")
    assert info1 is not None
    assert info2 is not None

    # 添加第三个会话，应该删除最旧的会话
    await limited_context_service.add_message("session3", MessageRole.USER, "M3")

    # session1 应该被删除（最旧的）
    info1_after = await limited_context_service.get_session_info("session1")
    assert info1_after is None

    # session2 和 session3 应该存在
    info2_after = await limited_context_service.get_session_info("session2")
    info3 = await limited_context_service.get_session_info("session3")
    assert info2_after is not None
    assert info3 is not None


@pytest.mark.asyncio
async def test_max_sessions_deletion_order(limited_context_service: ContextService):
    """测试会话数达到限制时按创建时间删除"""
    # 创建会话1
    await limited_context_service.add_message("old_session", MessageRole.USER, "Old")
    time.sleep(0.01)  # 确保时间戳不同

    # 创建会话2
    await limited_context_service.add_message("medium_session", MessageRole.USER, "Medium")
    time.sleep(0.01)

    # 创建会话3（应该删除最旧的 old_session）
    await limited_context_service.add_message("new_session", MessageRole.USER, "New")

    # 验证删除了最旧的会话
    assert await limited_context_service.get_session_info("old_session") is None
    assert await limited_context_service.get_session_info("medium_session") is not None
    assert await limited_context_service.get_session_info("new_session") is not None


@pytest.mark.asyncio
async def test_empty_session_behavior(context_service: ContextService):
    """测试空会话的各种行为"""
    session_id = "empty_session"

    # 获取不存在会话的历史
    history = await context_service.get_history(session_id)
    assert history == []

    # 获取不存在会话的信息
    info = await context_service.get_session_info(session_id)
    assert info is None

    # 为不存在的会话构建上下文
    context = await context_service.build_context(session_id)
    assert context == []

    # 清空不存在的会话（不应该报错）
    await context_service.clear_session(session_id)

    # 删除不存在的会话（不应该报错）
    await context_service.delete_session(session_id)


# =============================================================================
# 消息角色测试
# =============================================================================


@pytest.mark.asyncio
async def test_all_message_roles(context_service: ContextService):
    """测试所有消息角色"""
    session_id = "test_session"

    await context_service.add_message(session_id, MessageRole.SYSTEM, "System prompt")
    await context_service.add_message(session_id, MessageRole.USER, "User message")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "Assistant response")

    history = await context_service.get_history(session_id)

    assert len(history) == 3
    assert history[0].role == MessageRole.SYSTEM
    assert history[1].role == MessageRole.USER
    assert history[2].role == MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_message_role_values(context_service: ContextService):
    """测试消息角色的字符串值"""
    session_id = "test_session"

    await context_service.add_message(session_id, MessageRole.SYSTEM, "S")
    await context_service.add_message(session_id, MessageRole.USER, "U")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A")

    context = await context_service.build_context(session_id)

    # 验证角色字符串值与 OpenAI 格式一致
    assert context[0]["role"] == "system"
    assert context[1]["role"] == "user"
    assert context[2]["role"] == "assistant"


# =============================================================================
# 消息ID和唯一性测试
# =============================================================================


@pytest.mark.asyncio
async def test_message_id_unique(context_service: ContextService):
    """测试每条消息有唯一的 message_id"""
    session_id = "test_session"

    msg1 = await context_service.add_message(session_id, MessageRole.USER, "M1")
    msg2 = await context_service.add_message(session_id, MessageRole.USER, "M2")
    msg3 = await context_service.add_message(session_id, MessageRole.USER, "M3")

    assert msg1.message_id != msg2.message_id
    assert msg2.message_id != msg3.message_id
    assert msg3.message_id != msg1.message_id


@pytest.mark.asyncio
async def test_message_id_format(context_service: ContextService):
    """测试 message_id 是有效的 UUID 字符串"""
    session_id = "test_session"

    message = await context_service.add_message(session_id, MessageRole.USER, "Test")

    # UUID 格式：32个十六进制字符，可能带连字符
    assert len(message.message_id) > 0
    # UUID 应该包含连字符（标准格式）
    assert "-" in message.message_id or len(message.message_id) == 32


# =============================================================================
# 并发安全测试（基础）
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_add_messages(context_service: ContextService):
    """测试并发添加消息"""
    import asyncio

    session_id = "test_session"

    # 并发添加100条消息
    tasks = [context_service.add_message(session_id, MessageRole.USER, f"Message {i}") for i in range(100)]
    await asyncio.gather(*tasks)

    # 验证所有消息都被添加
    history = await context_service.get_history(session_id)
    assert len(history) == 100


@pytest.mark.asyncio
async def test_concurrent_different_sessions(context_service: ContextService):
    """测试并发操作不同会话"""
    import asyncio

    # 并发操作多个会话
    tasks = []
    for i in range(10):
        session_id = f"session_{i % 3}"  # 3个会话
        tasks.append(context_service.add_message(session_id, MessageRole.USER, f"Message {i}"))

    await asyncio.gather(*tasks)

    # 验证每个会话的消息数
    for i in range(3):
        history = await context_service.get_history(f"session_{i}")
        # 每个会话应该有一些消息
        assert len(history) > 0


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
