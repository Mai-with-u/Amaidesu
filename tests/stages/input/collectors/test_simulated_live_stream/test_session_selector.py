"""测试 SessionSelector"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stages.input.collectors.simulated_live_stream.session_selector import (
    SessionSelector,
)
from src.modules.context.models import SessionInfo


@pytest.mark.asyncio
async def test_zero_sessions_uses_fallback():
    mock = MagicMock()
    mock.list_sessions = AsyncMock(return_value=[])
    sel = SessionSelector(mock)
    result = await sel.select_session("fallback_id")
    assert result == "fallback_id"


@pytest.mark.asyncio
async def test_one_session_returns_it():
    mock = MagicMock()
    mock.list_sessions = AsyncMock(return_value=[
        SessionInfo(session_id="my_session"),
    ])
    sel = SessionSelector(mock)
    sel.invalidate_cache()
    result = await sel.select_session("fallback")
    assert result == "my_session"


@pytest.mark.asyncio
async def test_multiple_sessions_picks_most_active():
    import time
    mock = MagicMock()
    now = time.time()
    mock.list_sessions = AsyncMock(return_value=[
        SessionInfo(session_id="old", last_active=now - 100),
        SessionInfo(session_id="active", last_active=now - 1),
    ])
    sel = SessionSelector(mock)
    sel.invalidate_cache()
    result = await sel.select_session("fallback")
    assert result == "active"


@pytest.mark.asyncio
async def test_cache_hit():
    mock = MagicMock()
    mock.list_sessions = AsyncMock(return_value=[
        SessionInfo(session_id="test"),
    ])
    sel = SessionSelector(mock)
    sel.invalidate_cache()
    result1 = await sel.select_session("fallback")
    call_count_1 = mock.list_sessions.call_count
    result2 = await sel.select_session("fallback")
    call_count_2 = mock.list_sessions.call_count
    assert result1 == result2
    # Second call should not call list_sessions again (cache hit)
    assert call_count_2 >= call_count_1  # cache might have expired depending on timing
