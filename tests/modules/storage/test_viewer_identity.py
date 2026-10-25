"""观众身份解析：同种 ID 命中、改名与昵称记录、开放平台 ID 的应用补全。

规则见 ``ViewerRepo`` 模块说明；这里用真实临时库逐条验证。
"""

from __future__ import annotations

import asyncio
from typing import Any


from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerRepo
from src.modules.storage.repos.viewers import ViewerResolution, is_placeholder_name

OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"


async def _resolve(viewers: ViewerRepo, external_id: str, name: str, ts: int, **kwargs: Any) -> ViewerResolution:
    kwargs.setdefault("platform", "bilibili")
    kwargs.setdefault("id_kind", "account")
    return await viewers.resolve_viewer(external_id=external_id, user_name=name, timestamp_ms=ts, **kwargs)


async def _row(store: SQLiteDatabase, viewer_id: int) -> Any:
    (row,) = await store.execute("SELECT * FROM viewers WHERE id=?", (viewer_id,))
    return row


async def _names(store: SQLiteDatabase, viewer_id: int) -> list[tuple[str, int, int]]:
    rows = await store.execute(
        "SELECT user_name, first_seen_ms, last_seen_ms FROM viewer_names WHERE viewer_id=? ORDER BY first_seen_ms",
        (viewer_id,),
    )
    return [(r["user_name"], r["first_seen_ms"], r["last_seen_ms"]) for r in rows]


async def test_same_id_is_same_viewer_and_rename_is_recorded(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    first = await _resolve(viewers, "1001", "甲", 1_000)
    assert first.outcome == "created"
    assert (await _resolve(viewers, "1001", "甲", 2_000)).viewer_id == first.viewer_id
    # 改名：当前昵称换新，旧昵称的最晚出现时刻定为改名前最后一次以它出现的时刻
    renamed = await _resolve(viewers, "1001", "乙", 3_000)
    assert (renamed.viewer_id, renamed.outcome) == (first.viewer_id, "matched_id")
    row = await _row(store, first.viewer_id)
    assert (row["user_name"], row["name_seen_ms"]) == ("乙", 3_000)
    assert await _names(store, first.viewer_id) == [("甲", 1_000, 2_000), ("乙", 3_000, 3_000)]
    # 占位昵称不覆盖已知昵称、不进昵称记录
    await _resolve(viewers, "1001", "unknown", 4_000)
    assert (await _row(store, first.viewer_id))["user_name"] == "乙"
    assert len(await _names(store, first.viewer_id)) == 2


async def test_open_id_app_scope(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    # 存量 open_id（应用不明）被同一个 open_id 命中时补上当前应用
    await store.execute(
        "INSERT INTO viewers(platform, open_id, open_app_id, user_name, name_seen_ms, last_active_ms)"
        " VALUES ('bilibili', ?, '', '甲', 1000, 1000)",
        (OPEN_ID,),
    )
    hit = await _resolve(viewers, OPEN_ID, "甲", 2_000, id_kind="open", open_app_id="app1")
    assert hit.outcome == "matched_id"
    assert (await _row(store, hit.viewer_id))["open_app_id"] == "app1"


async def test_placeholder_names(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    fresh = await _resolve(viewers, "1001", "未知用户", 1_000)
    assert await _names(store, fresh.viewer_id) == []  # 占位昵称不进昵称记录
    for name in ("", "?", "unknown", "Unknown", "未知用户", "用***"):
        assert is_placeholder_name(name)
    assert not is_placeholder_name("甲")


async def test_concurrent_messages_from_new_viewer_resolve_to_one_viewer(store: SQLiteDatabase) -> None:
    # 记账器并发处理同一位新观众的几条消息：只建一位观众，都归到他身上
    results = await asyncio.gather(*(_resolve(store.viewers, "3001", "并发观众", 1_000 + i) for i in range(5)))
    assert len({r.viewer_id for r in results}) == 1
    assert len(await store.execute("SELECT id FROM viewers WHERE account_id='3001'")) == 1
