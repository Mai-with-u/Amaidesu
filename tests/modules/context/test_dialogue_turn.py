"""
DialogueTurn 模型与 MemoryStorage.seed_turns 批量灌入测试

验证 L1 对话配对窗口的数据形态与重启回灌入口：
- DialogueTurn 默认值（viewer_messages 空列表、assistant_message None）
- seed_turns 将 turns 扁平化为逐条消息并按顺序追加
- 受 max_messages_per_session FIFO 约束
- 空输入不创建空 session
- 新 session seed 后 SessionInfo 存在且 message_count 正确

运行: uv run pytest tests/modules/context/test_dialogue_turn.py -v
"""

import time

import pytest

from src.modules.context.models import (
    ConversationMessage,
    DialogueTurn,
    MessageRole,
    SessionInfo,
)
from src.modules.context.storage.memory import MemoryStorage


# =============================================================================
# DialogueTurn 模型默认值
# =============================================================================


def test_dialogue_turn_model_fields():
    """DialogueTurn 默认值：viewer_messages 空列表、assistant_message None。"""
    turn = DialogueTurn(session_id="s1")

    assert turn.session_id == "s1"
    assert turn.viewer_messages == []
    assert turn.assistant_message is None
    assert turn.assistant_emotion is None
    # 时间戳走 default_factory，自动填充
    assert isinstance(turn.start_timestamp, float)
    assert isinstance(turn.end_timestamp, float)
    assert turn.start_timestamp > 0
    assert turn.end_timestamp > 0


# =============================================================================
# seed_turns 行为
# =============================================================================


@pytest.mark.asyncio
async def test_seed_turns_flattens_to_messages():
    """3 轮 turn 扁平化：viewer=2+assistant、viewer=1+assistant、viewer=1 无 assistant
    → get_messages 返回 6 条且顺序正确（USER,USER,ASSISTANT,USER,ASSISTANT,USER）。
    """
    storage = MemoryStorage()
    session_id = "s1"

    base_ts = time.time()
    turns = [
        DialogueTurn(
            session_id=session_id,
            viewer_messages=["弹幕1", "弹幕2"],
            assistant_message="回复1",
            assistant_emotion="happy",
            start_timestamp=base_ts + 1.0,
            end_timestamp=base_ts + 2.0,
        ),
        DialogueTurn(
            session_id=session_id,
            viewer_messages=["弹幕3"],
            assistant_message="回复2",
            start_timestamp=base_ts + 10.0,
            end_timestamp=base_ts + 12.0,
        ),
        DialogueTurn(
            session_id=session_id,
            viewer_messages=["弹幕4"],
            assistant_message=None,
            start_timestamp=base_ts + 20.0,
            end_timestamp=base_ts + 21.0,
        ),
    ]

    appended = await storage.seed_turns(session_id, turns)

    # 返回值：2 viewer + 1 assistant + 1 viewer + 1 assistant + 1 viewer = 6
    assert appended == 6

    messages = await storage.get_messages(session_id)
    assert len(messages) == 6

    # 顺序与角色断言
    expected = [
        (MessageRole.USER, "弹幕1"),
        (MessageRole.USER, "弹幕2"),
        (MessageRole.ASSISTANT, "回复1"),
        (MessageRole.USER, "弹幕3"),
        (MessageRole.ASSISTANT, "回复2"),
        (MessageRole.USER, "弹幕4"),
    ]
    for idx, (role, content) in enumerate(expected):
        assert messages[idx].role == role, f"第 {idx} 条角色应为 {role}"
        assert messages[idx].content == content, f"第 {idx} 条内容应为 {content}"

    # assistant_emotion 透传
    assert messages[2].emotion == "happy"
    # 无 emotion 的不强制设置
    assert messages[4].emotion is None


@pytest.mark.asyncio
async def test_seed_turns_respects_fifo_limit():
    """max_messages_per_session=5，seed 6 条消息 → 只剩最新 5 条。"""
    storage = MemoryStorage(max_messages_per_session=5)
    session_id = "s1"

    base_ts = time.time()
    # 3 轮：viewer=1+assistant、viewer=1+assistant、viewer=1+assistant → 共 6 条
    turns = [
        DialogueTurn(
            session_id=session_id,
            viewer_messages=[f"v{i}"],
            assistant_message=f"a{i}",
            start_timestamp=base_ts + i * 10.0,
            end_timestamp=base_ts + i * 10.0 + 1.0,
        )
        for i in range(3)
    ]

    appended = await storage.seed_turns(session_id, turns)
    # appended 应返回实际尝试写入的条数（6）
    assert appended == 6

    messages = await storage.get_messages(session_id)
    # FIFO 淘汰后保留最后 5 条
    assert len(messages) == 5
    # 原始顺序: v0, a0, v1, a1, v2, a2 → 保留最后 5 条: a0, v1, a1, v2, a2
    expected = ["a0", "v1", "a1", "v2", "a2"]
    assert [m.content for m in messages] == expected


@pytest.mark.asyncio
async def test_seed_turns_empty_input():
    """空列表 → 返回 0 且不创建 session。"""
    storage = MemoryStorage()
    session_id = "never_created"

    appended = await storage.seed_turns(session_id, [])
    assert appended == 0

    # 会话不应被创建
    info = await storage.get_session_info(session_id)
    assert info is None
    messages = await storage.get_messages(session_id)
    assert messages == []


@pytest.mark.asyncio
async def test_seed_turns_session_creation():
    """新 session seed 后 get_session_info 存在且 message_count 正确。"""
    storage = MemoryStorage()
    session_id = "fresh"

    base_ts = time.time()
    turns = [
        DialogueTurn(
            session_id=session_id,
            viewer_messages=["v1", "v2"],
            assistant_message="a1",
            start_timestamp=base_ts,
            end_timestamp=base_ts + 1.0,
        ),
    ]
    await storage.seed_turns(session_id, turns)

    info = await storage.get_session_info(session_id)
    assert info is not None
    assert isinstance(info, SessionInfo)
    assert info.session_id == session_id
    assert info.message_count == 3
    assert info.created_at > 0
    # 最后活跃时间应等于最后写入消息的 timestamp（assistant 的 end_timestamp）
    assert info.last_active == base_ts + 1.0


# =============================================================================
# 兼容性：seed_turns 与 add_message 共享同一存储
# =============================================================================


@pytest.mark.asyncio
async def test_seed_turns_interleaves_with_add_message():
    """seed_turns 与 add_message 共享同一存储，可交错使用。"""
    storage = MemoryStorage()
    session_id = "s1"

    base_ts = time.time()
    # 先 add_message 一条
    await storage.add_message(
        ConversationMessage(
            session_id=session_id,
            role=MessageRole.SYSTEM,
            content="system prompt",
            timestamp=base_ts,
        )
    )
    # 再 seed_turns 一轮
    await storage.seed_turns(
        session_id,
        [
            DialogueTurn(
                session_id=session_id,
                viewer_messages=["hi"],
                assistant_message="hello",
                start_timestamp=base_ts + 1.0,
                end_timestamp=base_ts + 2.0,
            )
        ],
    )
    # 最后 add_message 一条
    await storage.add_message(
        ConversationMessage(
            session_id=session_id,
            role=MessageRole.USER,
            content="follow up",
            timestamp=base_ts + 3.0,
        )
    )

    messages = await storage.get_messages(session_id)
    assert [m.content for m in messages] == ["system prompt", "hi", "hello", "follow up"]
    assert [m.role for m in messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
