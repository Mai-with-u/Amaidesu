"""background._write_live_session 观众数记账单测

覆盖：
- audience_total 经 RoomState 快照写入 live_sessions 心跳（原硬编码 0 的回归）
- viewer_count 维持 0（open-live 协议无当前在线推送，无可靠数据源）
- 场次管理器缺失 / 归属解析失败时降级跳过，不触发仓储写入
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState


class _FakeSessionManager:
    def __init__(self, pk: int | None = None, *, raises: Exception | None = None) -> None:
        self._pk = pk
        self._raises = raises

    async def resolve_pk(self) -> int | None:
        if self._raises is not None:
            raise self._raises
        return self._pk


def _make_maintainer(
    repo: MagicMock,
    session_manager: _FakeSessionManager,
    *,
    audience_total: int = 0,
) -> BackgroundMaintainer:
    room_state = RoomState()
    if audience_total:
        room_state.set_audience_total(audience_total, now_ms=1_000)
    return BackgroundMaintainer(
        {},
        room_state=room_state,
        sessions_repo=repo,
        session_manager=session_manager,
        prompt_manager=MagicMock(),
    )


@pytest.mark.asyncio
async def test_write_live_session_persists_audience_total() -> None:
    """推送到达后的心跳把快照累计观看人次写入 live_sessions"""
    repo = MagicMock()
    repo.update_live_session_stats = AsyncMock(return_value=True)
    maintainer = _make_maintainer(repo, _FakeSessionManager(777), audience_total=1234)

    await maintainer._write_live_session(now_ms=2_000)

    kwargs = repo.update_live_session_stats.await_args.kwargs
    assert kwargs["live_session_id"] == 777
    assert kwargs["audience_total"] == 1234
    # open-live 无当前在线推送，viewer_count 无数据源
    assert kwargs["viewer_count"] == 0


@pytest.mark.asyncio
async def test_write_live_session_zero_before_any_push() -> None:
    """推送未到过时 audience_total=0（未知语义），心跳照常写入"""
    repo = MagicMock()
    repo.update_live_session_stats = AsyncMock(return_value=True)
    maintainer = _make_maintainer(repo, _FakeSessionManager(777))

    await maintainer._write_live_session(now_ms=2_000)

    kwargs = repo.update_live_session_stats.await_args.kwargs
    assert kwargs["audience_total"] == 0


@pytest.mark.asyncio
async def test_write_live_session_skips_without_session_manager() -> None:
    """场次管理器缺失：心跳整体降级跳过，仓储不被触达"""
    repo = MagicMock()
    repo.update_live_session_stats = AsyncMock(return_value=True)
    maintainer = _make_maintainer(repo, None)  # type: ignore[arg-type]

    await maintainer._write_live_session(now_ms=2_000)

    repo.update_live_session_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_live_session_skips_when_resolve_fails() -> None:
    """场次归属解析失败：降级跳过，不阻断轻循环"""
    repo = MagicMock()
    repo.update_live_session_stats = AsyncMock(return_value=True)
    maintainer = _make_maintainer(repo, _FakeSessionManager(raises=RuntimeError("boom")))

    await maintainer._write_live_session(now_ms=2_000)

    repo.update_live_session_stats.assert_not_awaited()
