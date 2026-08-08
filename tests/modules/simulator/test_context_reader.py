"""测试 ContextReader"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.context_reader import (
    ContextServiceReader,
)
from src.modules.context.models import ConversationMessage, MessageRole


@pytest.mark.asyncio
async def test_context_reader_filters_assistant():
    import time
    cfg = SimulatorConfigSchema()
    mock = MagicMock()
    now = time.time()
    mock.get_history = AsyncMock(return_value=[
        ConversationMessage(session_id="t", role=MessageRole.USER, content="问题", timestamp=now - 5),
        ConversationMessage(session_id="t", role=MessageRole.ASSISTANT, content="主播回答", emotion="happy", timestamp=now - 1),
    ])
    reader = ContextServiceReader(mock, cfg)
    snapshot = await reader.get_streamer_context("test", 5)
    assert len(snapshot.recent_messages) == 1
    assert snapshot.recent_messages[0] == "主播回答"
    assert snapshot.recent_emotion == "happy"
    assert snapshot.is_online is True


@pytest.mark.asyncio
async def test_context_reader_empty_returns_empty():
    cfg = SimulatorConfigSchema()
    mock = MagicMock()
    mock.get_history = AsyncMock(return_value=[])
    reader = ContextServiceReader(mock, cfg)
    snapshot = await reader.get_streamer_context("test", 5)
    assert snapshot.is_online is False
    assert len(snapshot.recent_messages) == 0


@pytest.mark.asyncio
async def test_context_reader_no_assistant_returns_empty():
    import time
    cfg = SimulatorConfigSchema()
    mock = MagicMock()
    mock.get_history = AsyncMock(return_value=[
        ConversationMessage(session_id="t", role=MessageRole.USER, content="问题", timestamp=time.time() - 1),
    ])
    reader = ContextServiceReader(mock, cfg)
    snapshot = await reader.get_streamer_context("test", 5)
    assert snapshot.is_online is False


@pytest.mark.asyncio
async def test_context_reader_detects_new_activity():
    import time
    cfg = SimulatorConfigSchema()
    mock = MagicMock()
    now = time.time()
    mock.get_history = AsyncMock(return_value=[
        ConversationMessage(session_id="t", role=MessageRole.ASSISTANT, content="hi", timestamp=now - 1),
    ])
    reader = ContextServiceReader(mock, cfg)
    snap1 = await reader.get_streamer_context("test", 5)
    assert snap1.has_new_activity_since_last_check is True
    snap2 = await reader.get_streamer_context("test", 5)
    assert snap2.has_new_activity_since_last_check is False
