"""
live_sessions 场次状态写链单测（场次主键语义：一行 = 一场直播）

覆盖：
- insert_live_session 开行（AUTOINCREMENT 主键；ended_at_ms NULL 即进行中）
- update_live_session_stats 只更新已存在行（行不存在返回 False，不建行）
- close_live_session 结账（幂等：已结账行不覆盖）
- count_session_details 空场次判定；delete_live_session 级联清明细
- list_dangling_live_sessions 语义
- BackgroundMaintainer 轻 tick 经 LiveSessionManager 解析归属真正写进表
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-live-session-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


class _FakeSessionManager:
    """测试用场次管理器替身：固定返回预置主键。"""

    def __init__(self, pk: int) -> None:
        self._pk = pk

    async def resolve_pk(self) -> int:
        return self._pk


# ===== insert_live_session / update_live_session_stats =====


@pytest.mark.asyncio
async def test_insert_live_session_opens_active_row(store: SQLiteStore) -> None:
    pk = await store.insert_live_session(
        stream_id="room-1",
        platform="bilibili",
        started_at_ms=1_700_000_000_000,
        title="晚间场",
        source="manual",
    )
    assert pk > 0
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["stream_id"] == "room-1"
    assert row["platform"] == "bilibili"
    assert row["title"] == "晚间场"
    assert row["source"] == "manual"
    assert row["started_at_ms"] == 1_700_000_000_000
    assert row["ended_at_ms"] is None


@pytest.mark.asyncio
async def test_stats_update_existing_row_only(store: SQLiteStore) -> None:
    pk = await store.insert_live_session(started_at_ms=1_000)
    ok = await store.update_live_session_stats(
        live_session_id=pk,
        heat=3,
        viewer_count=42,
        audience_total=500,
        updated_at_ms=6_000,
    )
    assert ok is True
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["heat"] == 3
    assert row["viewer_count"] == 42
    assert row["audience_total"] == 500
    assert row["updated_at_ms"] == 6_000
    # 开行字段不被心跳覆盖
    assert row["started_at_ms"] == 1_000

    # 不存在的行：返回 False 且不建行
    miss = await store.update_live_session_stats(
        live_session_id=999_999,
        heat=1,
        viewer_count=0,
        audience_total=0,
        updated_at_ms=6_000,
    )
    assert miss is False
    rows = await store.execute("SELECT * FROM live_sessions")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_close_live_session_idempotent(store: SQLiteStore) -> None:
    pk = await store.insert_live_session(started_at_ms=1_000)
    assert await store.close_live_session(live_session_id=pk, ended_at_ms=9_000) is True
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["ended_at_ms"] == 9_000
    # 重复结账不覆盖首次结束时间
    assert await store.close_live_session(live_session_id=pk, ended_at_ms=12_000) is False
    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["ended_at_ms"] == 9_000


@pytest.mark.asyncio
async def test_close_missing_session_returns_false(store: SQLiteStore) -> None:
    assert await store.close_live_session(live_session_id=424_242, ended_at_ms=9_000) is False


# ===== 明细计数与级联删除 =====


@pytest.mark.asyncio
async def test_count_and_cascade_delete(store: SQLiteStore) -> None:
    pk = await store.insert_live_session(started_at_ms=1_000)
    await store.insert_live_chat(
        live_session_id=pk,
        timestamp_ms=1_100,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    await store.insert_gift(
        live_session_id=pk,
        timestamp_ms=1_200,
        user_id="u1",
        user_name="观众",
        gift_name="小星星",
        gift_count=1,
    )
    assert await store.count_session_details(live_session_id=pk) == 2

    # 级联删除：明细与场次行一起消失，且不影响其他场次
    other = await store.insert_live_session(started_at_ms=2_000)
    await store.insert_live_chat(
        live_session_id=other,
        timestamp_ms=2_100,
        sender_role="viewer",
        content="keep",
        message_type="danmaku",
    )
    assert await store.delete_live_session(live_session_id=pk) is True
    assert await store.get_live_session(live_session_id=pk) is None
    rows = await store.execute("SELECT content FROM live_chat")
    assert [r["content"] for r in rows] == ["keep"]


@pytest.mark.asyncio
async def test_list_dangling_live_sessions_returns_unclosed(store: SQLiteStore) -> None:
    """未结账（ended_at_ms IS NULL）的场次被列入 dangling；已结账行不入选。"""
    await store.insert_live_session(started_at_ms=1_000, source="manual")  # 进行中显式场次（dangling）
    ended = await store.insert_live_session(started_at_ms=2_000, source="legacy")  # 已结账遗留行
    await store.execute("UPDATE live_sessions SET ended_at_ms=? WHERE id=?", (3_000, ended))

    dangling = await store.list_dangling_live_sessions()
    assert len(dangling) == 1
    assert dangling[0]["source"] == "manual"


# ===== 场次列表 =====


@pytest.mark.asyncio
async def test_list_live_sessions_orders_desc_with_counts(store: SQLiteStore) -> None:
    old = await store.insert_live_session(started_at_ms=1_000)
    new = await store.insert_live_session(started_at_ms=9_000)
    await store.insert_live_chat(
        live_session_id=new,
        timestamp_ms=9_100,
        sender_role="viewer",
        content="hi",
        message_type="danmaku",
    )
    rows = await store.list_live_sessions()
    assert [int(r["id"]) for r in rows] == [new, old]
    counts = {int(r["id"]): int(r["message_count"]) for r in rows}
    assert counts[new] == 1
    assert counts[old] == 0


# ===== BackgroundMaintainer 轻 tick → live_sessions（经 LiveSessionManager 解析） =====


@pytest.mark.asyncio
async def test_background_light_tick_persists_stats_via_session_manager(store: SQLiteStore) -> None:
    pk = await store.insert_live_session(started_at_ms=1_000)
    room_state = RoomState()
    maintainer = BackgroundMaintainer(
        {},
        room_state=room_state,
        live_session_store=store,
        session_manager=_FakeSessionManager(pk),
    )
    await maintainer._light_tick(now_ms=5_000)
    await maintainer._light_tick(now_ms=10_000)

    row = await store.get_live_session(live_session_id=pk)
    assert row is not None
    assert row["updated_at_ms"] == 10_000
    # 冷场默认热度 low → 1
    assert row["heat"] == 1


@pytest.mark.asyncio
async def test_background_tick_skipped_without_session_manager(store: SQLiteStore) -> None:
    """无场次管理器时心跳整体降级跳过（心跳不建行，场次行归 LiveSessionManager）。"""
    room_state = RoomState()
    maintainer = BackgroundMaintainer(
        {},
        room_state=room_state,
        live_session_store=store,
    )
    await maintainer._light_tick(now_ms=5_000)
    rows = await store.execute("SELECT * FROM live_sessions")
    assert len(rows) == 0


# ===== 辅助：保留类型引用检查（Any 导入防 ruff 误报移除） =====


def test_fake_session_manager_contract() -> None:
    manager: Any = _FakeSessionManager(7)

    import asyncio

    assert asyncio.run(manager.resolve_pk()) == 7


# ===== 场次列表排序与筛选 =====


@pytest.mark.asyncio
async def test_list_live_sessions_orders_by_started_at_desc(store: SQLiteStore) -> None:
    """无默认场次兜底：列表按开始时间倒序（最新场次在前）。"""
    old = await store.insert_live_session(started_at_ms=1_000, source="manual", title="早场")
    newest = await store.insert_live_session(started_at_ms=9_000, source="manual", title="新场")

    rows = await store.list_live_sessions()
    ids = [int(r["id"]) for r in rows]
    assert ids == [newest, old], f"按 started_at_ms 倒序，实际 {ids}"


@pytest.mark.asyncio
async def test_list_sessions_filters_by_source_and_title(store: SQLiteStore) -> None:
    await store.insert_live_session(started_at_ms=1_000, source="manual", title="周五晚间场")
    await store.insert_live_session(started_at_ms=2_000, source="replay", title="回放 2026-09-01")
    await store.insert_live_session(started_at_ms=3_000, source="manual", title="周末午间场")

    manual_only = await store.list_live_sessions(source="manual")
    assert [str(r["title"]) for r in manual_only] == ["周末午间场", "周五晚间场"]

    keyword = await store.list_live_sessions(title_keyword="晚间")
    assert [str(r["title"]) for r in keyword] == ["周五晚间场"]

    both = await store.list_live_sessions(source="replay", title_keyword="不存在的标题")
    assert both == []
