"""live_sessions 写链（一行 = 一场直播）：开行、心跳、结账、重命名、级联删除、列表筛选与后台心跳落库"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.modules.storage.database import SQLiteDatabase
from src.agents.streamer.config import StreamerBackgroundConfig


class _FakeSessionManager:
    def __init__(self, pk: int) -> None:
        self._pk = pk

    async def resolve_pk(self) -> int:
        return self._pk


async def _chat(store: SQLiteDatabase, live_session_id: int, ts: int, content: str = "hi") -> None:
    await store.chat.insert_live_chat(
        live_session_id=live_session_id, timestamp_ms=ts, sender_role="viewer", content=content, message_type="danmaku"
    )


async def test_session_row_lifecycle(store: SQLiteDatabase) -> None:
    sessions = store.sessions
    pk = await sessions.insert_live_session(
        stream_id="room-1", platform="bilibili", started_at_ms=1_000, title="晚间场", source="manual"
    )
    row = await sessions.get_live_session(live_session_id=pk)
    assert (row.stream_id, row.platform, row.title, row.source) == (
        "room-1",
        "bilibili",
        "晚间场",
        "manual",
    )
    assert (row.started_at_ms, row.ended_at_ms) == (1_000, None)  # NULL = 进行中

    # 心跳只更新已存在行，不覆盖开行字段；行不存在返回 False 且不建行
    stats = {"heat": 3, "viewer_count": 42, "audience_total": 500, "updated_at_ms": 6_000}
    assert await sessions.update_live_session_stats(live_session_id=pk, **stats) is True
    row = await sessions.get_live_session(live_session_id=pk)
    assert (row.heat, row.viewer_count, row.audience_total, row.updated_at_ms) == (3, 42, 500, 6_000)
    assert row.started_at_ms == 1_000
    assert await sessions.update_live_session_stats(live_session_id=999_999, **stats) is False
    assert len(await store.execute("SELECT * FROM live_sessions")) == 1

    # 结账幂等：重复结账不覆盖首次结束时间
    assert await sessions.close_live_session(live_session_id=pk, ended_at_ms=9_000) is True
    assert await sessions.close_live_session(live_session_id=pk, ended_at_ms=12_000) is False
    assert (await sessions.get_live_session(live_session_id=pk)).ended_at_ms == 9_000
    assert await sessions.close_live_session(live_session_id=424_242, ended_at_ms=9_000) is False


async def test_rename_session_title(store: SQLiteDatabase) -> None:
    sessions = store.sessions
    pk = await sessions.insert_live_session(started_at_ms=1_000, title="旧标题")
    assert await sessions.update_live_session_title(live_session_id=pk, title="新标题", updated_at_ms=2_000) is True
    row = await sessions.get_live_session(live_session_id=pk)
    assert (row.title, row.updated_at_ms) == ("新标题", 2_000)

    # None 清除标题；行不存在返回 False 且不建行
    assert await sessions.update_live_session_title(live_session_id=pk, title=None, updated_at_ms=3_000) is True
    assert (await sessions.get_live_session(live_session_id=pk)).title is None
    assert await sessions.update_live_session_title(live_session_id=999_999, title="x", updated_at_ms=4_000) is False
    assert len(await store.execute("SELECT * FROM live_sessions")) == 1


async def test_count_details_and_cascade_delete(store: SQLiteDatabase) -> None:
    pk = await store.sessions.insert_live_session(started_at_ms=1_000)
    await _chat(store, pk, 1_100)
    await store.chat.insert_gift(
        live_session_id=pk, timestamp_ms=1_200, user_id="u1", user_name="观众", gift_name="小星星", quantity=1
    )
    assert await store.chat.count_session_details(live_session_id=pk) == 2

    other = await store.sessions.insert_live_session(started_at_ms=2_000)
    await _chat(store, other, 2_100, "keep")
    assert await store.sessions.delete_live_session(live_session_id=pk) is True
    assert await store.sessions.get_live_session(live_session_id=pk) is None
    assert [r["content"] for r in await store.execute("SELECT content FROM live_chat")] == ["keep"]


async def test_list_dangling_live_sessions_returns_unclosed(store: SQLiteDatabase) -> None:
    await store.sessions.insert_live_session(started_at_ms=1_000, source="manual")
    ended = await store.sessions.insert_live_session(started_at_ms=2_000, source="legacy")
    await store.sessions.close_live_session(live_session_id=ended, ended_at_ms=3_000)
    assert [r.source for r in await store.sessions.list_dangling_live_sessions()] == ["manual"]


async def test_list_sessions_order_counts_and_filters(store: SQLiteDatabase) -> None:
    sessions = store.sessions
    early = await sessions.insert_live_session(started_at_ms=1_000, source="manual", title="周五晚间场")
    await sessions.insert_live_session(started_at_ms=2_000, source="replay", title="回放 2026-09-01")
    latest = await sessions.insert_live_session(started_at_ms=3_000, source="manual", title="周末午间场")
    await _chat(store, latest, 3_100)

    rows = await sessions.list_live_sessions()
    assert rows[0].id == latest and rows[-1].id == early  # 开始时间倒序
    assert {r.id: r.message_count for r in rows}[latest] == 1

    def titles(rows) -> list[str]:
        return [r.title for r in rows]

    assert titles(await sessions.list_live_sessions(source="manual")) == ["周末午间场", "周五晚间场"]
    assert titles(await sessions.list_live_sessions(title_keyword="晚间")) == ["周五晚间场"]
    assert await sessions.list_live_sessions(source="replay", title_keyword="不存在的标题") == []


def _maintainer(store: SQLiteDatabase, session_manager: Any = None) -> BackgroundMaintainer:
    # 轻 tick 不走摘要渲染：满足 render() -> str 的最小替身即可
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    return BackgroundMaintainer(
        StreamerBackgroundConfig(),
        room_state=RoomState(),
        sessions_repo=store.sessions,
        session_manager=session_manager,
        prompt_manager=prompt_manager,
    )


async def test_background_light_tick_persists_stats_via_session_manager(store: SQLiteDatabase) -> None:
    pk = await store.sessions.insert_live_session(started_at_ms=1_000)
    maintainer = _maintainer(store, _FakeSessionManager(pk))
    await maintainer._light_tick(now_ms=5_000)
    await maintainer._light_tick(now_ms=10_000)

    row = await store.sessions.get_live_session(live_session_id=pk)
    assert (row.updated_at_ms, row.heat) == (10_000, 1)  # 冷场默认热度 low → 1


async def test_background_tick_without_session_manager_creates_nothing(store: SQLiteDatabase) -> None:
    """心跳不建行：场次行归 LiveSessionManager 管。"""
    await _maintainer(store)._light_tick(now_ms=5_000)
    assert await store.execute("SELECT * FROM live_sessions") == []
