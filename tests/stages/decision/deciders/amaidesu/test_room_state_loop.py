"""RoomStateLoop 后台预处理循环单元测试

测试低频 LLM 话题摘要的冷场降频 / 活跃触发 / 时间戳 / 生命周期。
所有时间通过注入 now_ms 参数保证确定性，不依赖 time.sleep。
"""

import src.modules.config.schemas  # noqa: F401  # isort:skip
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.llm.manager import LLMResponse
from src.stages.decision.deciders.amaidesu.room_state import RoomState
from src.stages.decision.deciders.amaidesu.room_state_loop import RoomStateLoop


def _make_msg(text: str) -> MagicMock:
    m = MagicMock()
    m.text = text
    return m


def _make_mock_llm(content: str = "讨论游戏通关") -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(return_value=LLMResponse(success=True, content=content, model="test"))
    return mock


def _make_mock_context(messages: list) -> MagicMock:
    mock = MagicMock()
    mock.get_history = AsyncMock(return_value=messages)
    return mock


def _feed_active_messages(rs: RoomState, base_ts: int = 100_000, count: int = 10):
    for i in range(count):
        rs.update(_make_msg(f"游戏{i}"), now_ms=base_ts + i * 200)


class TestRoomStateLoopCold:
    """冷场场景：仍按低热度间隔执行摘要"""

    @pytest.mark.asyncio
    async def test_cold_no_messages_still_summarizes(self):
        rs = RoomState()
        mock_llm = _make_mock_llm()
        mock_ctx = _make_mock_context(messages=[])

        loop = RoomStateLoop(
            config={},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        loop._summarize = AsyncMock()
        await loop._tick(now_ms=200_000)

        loop._summarize.assert_awaited_once_with(now_ms=200_000)

    @pytest.mark.asyncio
    async def test_cold_after_timeout_still_summarizes(self):
        rs = RoomState()
        rs.update(_make_msg("早期消息"), now_ms=100_000)
        mock_llm = _make_mock_llm()
        mock_ctx = _make_mock_context(messages=[])

        loop = RoomStateLoop(
            config={"room_state_cold_timeout_ms": 60_000},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        loop._summarize = AsyncMock()
        await loop._tick(now_ms=200_000)

        loop._summarize.assert_awaited_once_with(now_ms=200_000)


class TestRoomStateLoopActive:
    """活跃场景：高密度弹幕 → LLM 摘要被调用"""

    @pytest.mark.asyncio
    async def test_active_calls_llm(self):
        rs = RoomState()
        _feed_active_messages(rs, base_ts=100_000, count=10)
        mock_llm = _make_mock_llm(content="正在讨论游戏通关")
        mock_msg = MagicMock()
        mock_msg.role.value = "user"
        mock_msg.content = "游戏真好玩"
        mock_ctx = _make_mock_context(messages=[mock_msg])

        loop = RoomStateLoop(
            config={},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop._tick(now_ms=102_000)

        mock_llm.chat.assert_called_once()
        snap = rs.get_snapshot(now_ms=102_000)
        assert snap.topic_summary == "正在讨论游戏通关"

    @pytest.mark.asyncio
    async def test_active_stores_summary_in_snapshot(self):
        rs = RoomState()
        _feed_active_messages(rs, base_ts=100_000, count=5)
        mock_llm = _make_mock_llm(content="新话题摘要")
        mock_msg = MagicMock()
        mock_msg.role.value = "user"
        mock_msg.content = "弹幕内容"
        mock_ctx = _make_mock_context(messages=[mock_msg])

        loop = RoomStateLoop(
            config={},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop._tick(now_ms=101_000)

        snap = rs.get_snapshot(now_ms=101_000)
        assert snap.topic_summary == "新话题摘要"
        assert snap.topic_summary_at_ms == 101_000


class TestRoomStateLoopTimestamp:
    """摘要时间戳"""

    @pytest.mark.asyncio
    async def test_summary_has_timestamp(self):
        rs = RoomState()
        _feed_active_messages(rs, base_ts=500_000, count=8)
        mock_llm = _make_mock_llm(content="摘要内容")
        mock_msg = MagicMock()
        mock_msg.role.value = "user"
        mock_msg.content = "弹幕"
        mock_ctx = _make_mock_context(messages=[mock_msg])

        loop = RoomStateLoop(
            config={},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        tick_ts = 502_000
        await loop._tick(now_ms=tick_ts)

        snap = rs.get_snapshot(now_ms=tick_ts)
        assert snap.topic_summary_at_ms > 0
        assert snap.topic_summary_at_ms == tick_ts


class TestRoomStateLoopInterval:
    """热度驱动的频率控制"""

    @pytest.mark.asyncio
    async def test_respects_interval(self):
        """距上次摘要不足间隔 → 跳过"""
        rs = RoomState()
        _feed_active_messages(rs, base_ts=100_000, count=5)
        mock_llm = _make_mock_llm(content="第一次摘要")
        mock_msg = MagicMock()
        mock_msg.role.value = "user"
        mock_msg.content = "弹幕"
        mock_ctx = _make_mock_context(messages=[mock_msg])

        loop = RoomStateLoop(
            config={"room_state_llm_summary_interval_ms": 60_000},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop._tick(now_ms=101_000)
        assert mock_llm.chat.call_count == 1

        # 继续灌入新消息保持活跃，但距上次仅 10s < 60s → 不应再次调用
        rs.update(_make_msg("新弹幕"), now_ms=108_000)
        await loop._tick(now_ms=111_000)
        assert mock_llm.chat.call_count == 1


class TestRoomStateLoopLifecycle:
    """后台任务生命周期"""

    @pytest.mark.asyncio
    async def test_stop_cancels_loop(self):
        rs = RoomState()
        mock_llm = _make_mock_llm()
        mock_ctx = _make_mock_context(messages=[])

        loop = RoomStateLoop(
            config={"room_state_tick_interval_ms": 50},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop.start()
        assert loop._task is not None
        assert not loop._task.done()

        await loop.stop()
        assert loop._task is None or loop._task.done()

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        rs = RoomState()
        mock_llm = _make_mock_llm()
        mock_ctx = _make_mock_context(messages=[])

        loop = RoomStateLoop(
            config={"room_state_tick_interval_ms": 50},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop.start()
        task1 = loop._task
        await loop.start()
        assert loop._task is task1
        await loop.stop()

    @pytest.mark.asyncio
    async def test_disabled_skips_all(self):
        """room_state_enabled=False → _tick 直接返回"""
        rs = RoomState()
        _feed_active_messages(rs, base_ts=100_000, count=5)
        mock_llm = _make_mock_llm()
        mock_ctx = _make_mock_context(messages=[MagicMock()])

        loop = RoomStateLoop(
            config={"room_state_enabled": False},
            room_state=rs,
            llm_service=mock_llm,
            context_service=mock_ctx,
        )
        await loop._tick(now_ms=101_000)
        mock_llm.chat.assert_not_called()
