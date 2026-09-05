"""
ContextService 对话配对视图测试

验证 service 层新暴露的两个回灌/聚合入口：
- seed_dialogue_turns：批量回灌 DialogueTurn（委托存储层 seed_turns）
- get_dialogue_history：按"观众消息组 → 主播回复"配对视图聚合

主存储形态仍是逐条消息；本测试聚焦视图聚合的正确性。
"""

import pytest

from src.modules.context import ContextService
from src.modules.context.models import DialogueTurn, MessageRole


@pytest.fixture
async def context_service():
    """默认配置的 ContextService 实例"""
    service = ContextService()
    await service.initialize()
    yield service
    await service.cleanup()


@pytest.mark.asyncio
async def test_seed_dialogue_turns_smoke(context_service: ContextService):
    """seed 两轮 → 返回条数正确，get_history 看到 4 条消息"""
    turns = [
        DialogueTurn(
            session_id="test",
            viewer_messages=["弹幕1"],
            assistant_message="回复1",
            start_timestamp=1000.0,
            end_timestamp=1001.0,
        ),
        DialogueTurn(
            session_id="test",
            viewer_messages=["弹幕2", "弹幕3"],
            assistant_message="回复2",
            start_timestamp=1010.0,
            end_timestamp=1011.0,
        ),
    ]

    appended = await context_service.seed_dialogue_turns("test", turns)

    # 1 viewer + 1 assistant + 2 viewer + 1 assistant = 5
    assert appended == 5

    history = await context_service.get_history("test")
    assert len(history) == 5
    assert history[0].role == MessageRole.USER
    assert history[0].content == "弹幕1"
    assert history[1].role == MessageRole.ASSISTANT
    assert history[1].content == "回复1"
    assert history[2].content == "弹幕2"
    assert history[3].content == "弹幕3"
    assert history[4].content == "回复2"


@pytest.mark.asyncio
async def test_get_dialogue_history_pairs_turns(context_service: ContextService):
    """U1,A1,U2,A2,U3 → 3 轮：[U1→A1, U2→A2, U3→None]"""
    session_id = "pairing"

    await context_service.add_message(session_id, MessageRole.USER, "U1")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A1")
    await context_service.add_message(session_id, MessageRole.USER, "U2")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A2")
    await context_service.add_message(session_id, MessageRole.USER, "U3")

    turns = await context_service.get_dialogue_history(session_id)

    assert len(turns) == 3

    # 轮 1：U1 → A1
    assert turns[0].viewer_messages == ["U1"]
    assert turns[0].assistant_message == "A1"

    # 轮 2：U2 → A2
    assert turns[1].viewer_messages == ["U2"]
    assert turns[1].assistant_message == "A2"

    # 轮 3：U3（未回复）
    assert turns[2].viewer_messages == ["U3"]
    assert turns[2].assistant_message is None


@pytest.mark.asyncio
async def test_get_dialogue_history_merges_consecutive_user(context_service: ContextService):
    """U1,U2,A1 → 一轮 viewer=[U1,U2] + assistant=A1"""
    session_id = "merged"

    await context_service.add_message(session_id, MessageRole.USER, "U1")
    await context_service.add_message(session_id, MessageRole.USER, "U2")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A1")

    turns = await context_service.get_dialogue_history(session_id)

    assert len(turns) == 1
    assert turns[0].viewer_messages == ["U1", "U2"]
    assert turns[0].assistant_message == "A1"


@pytest.mark.asyncio
async def test_get_dialogue_history_limit(context_service: ContextService):
    """3 轮，limit=2 → 返回最近 2 轮"""
    session_id = "limit"

    await context_service.add_message(session_id, MessageRole.USER, "U1")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A1")
    await context_service.add_message(session_id, MessageRole.USER, "U2")
    await context_service.add_message(session_id, MessageRole.ASSISTANT, "A2")
    await context_service.add_message(session_id, MessageRole.USER, "U3")

    turns = await context_service.get_dialogue_history(session_id, limit=2)

    assert len(turns) == 2
    # 最近 2 轮：[U2→A2, U3→None]
    assert turns[0].viewer_messages == ["U2"]
    assert turns[0].assistant_message == "A2"
    assert turns[1].viewer_messages == ["U3"]
    assert turns[1].assistant_message is None


@pytest.mark.asyncio
async def test_seed_dialogue_turns_uninitialized_raises():
    """未 initialize 调用 seed_dialogue_turns → RuntimeError"""
    service = ContextService()

    with pytest.raises(RuntimeError, match="未初始化"):
        await service.seed_dialogue_turns("any", [])


@pytest.mark.asyncio
async def test_get_dialogue_history_empty_session(context_service: ContextService):
    """空会话 → 返回空列表"""
    turns = await context_service.get_dialogue_history("never_used")
    assert turns == []


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
