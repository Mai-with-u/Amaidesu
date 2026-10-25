"""观众身份解析：同种 ID 命中、改名与昵称记录、按当前昵称关联老观众（时限 / 候选唯一 / 对应 ID 列空着）、待确认。

规则见 ``ViewerRepo`` 模块说明；这里用真实临时库逐条验证。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerRepo
from src.modules.storage.repos.viewers import ViewerResolution, is_placeholder_name

DAY = 86_400_000
WINDOW = 90 * DAY
OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"


async def _resolve(viewers: ViewerRepo, external_id: str, name: str, ts: int, **kwargs: Any) -> ViewerResolution:
    kwargs.setdefault("platform", "bilibili")
    kwargs.setdefault("id_kind", "account")
    return await viewers.resolve_viewer(
        external_id=external_id, user_name=name, timestamp_ms=ts, name_match_window_ms=WINDOW, **kwargs
    )


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


async def test_new_id_links_to_old_viewer_by_current_name_within_window(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    old = await _resolve(viewers, "1001", "甲", 1_000)
    await store.viewer_profiles.upsert_profile(
        platform="bilibili", user_id="1001", profile_text="老观众", last_compressed_at_ms=1
    )
    # 换到开放平台源：同名、昵称 30 天内出现过、唯一候选 → 同一个人
    linked = await _resolve(viewers, OPEN_ID, "甲", 1_000 + 30 * DAY, id_kind="open", open_app_id="app1")
    assert (linked.viewer_id, linked.outcome) == (old.viewer_id, "linked_by_name")
    row = await _row(store, old.viewer_id)
    assert (row["account_id"], row["open_id"], row["open_app_id"]) == ("1001", OPEN_ID, "app1")
    # 画像跟着人走：按新 ID 也能取到
    assert await store.viewer_profiles.get_profile_text(platform="bilibili", user_id=OPEN_ID) == "老观众"
    # 之后同一个 open_id 直接按 ID 命中
    assert (await _resolve(viewers, OPEN_ID, "甲", 2 * WINDOW, id_kind="open", open_app_id="app1")).outcome == (
        "matched_id"
    )


async def test_stale_or_ambiguous_name_goes_pending(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    old = await _resolve(viewers, "1001", "甲", 1_000)
    # 昵称超过时限没出现过：可能已易主，新建并挂上并入建议
    stale = await _resolve(viewers, OPEN_ID, "甲", 1_000 + WINDOW + 1, id_kind="open", open_app_id="app1")
    assert (stale.outcome, stale.candidate_id) == ("pending", old.viewer_id)
    assert stale.viewer_id != old.viewer_id
    row = await _row(store, stale.viewer_id)
    assert (row["link_status"], row["link_candidate_id"], row["open_id"]) == ("pending", old.viewer_id, OPEN_ID)

    # 同名候选不止一个：也转人工，建议并入昵称最近出现的那位
    a = await _resolve(viewers, "2001", "丙", 1_000)
    b = await _resolve(viewers, "2002", "丙", 5_000)
    ambiguous = await _resolve(viewers, "c0ffee00-0000-4000-8000-000000000000", "丙", 6_000, id_kind="open")
    assert (ambiguous.outcome, ambiguous.candidate_id) == ("pending", b.viewer_id)
    assert a.viewer_id != b.viewer_id  # 两个不同账号 ID 即使同名也不会互相关联


@pytest.mark.parametrize(
    ("first_kwargs", "second_kwargs"),
    [
        # 同一种 ID 值不同：另一个账号，说明昵称已易主
        ({"external_id": "1001"}, {"external_id": "1002"}),
        # 存量迁移来的 open_id 不知道应用：无法判断是否同一应用，不作候选
        (
            {"external_id": OPEN_ID, "id_kind": "open", "open_app_id": ""},
            {"external_id": "c0ffee00-0000-4000-8000-000000000000", "id_kind": "open", "open_app_id": "app1"},
        ),
        # 模拟观众与真实观众同名也不关联
        ({"external_id": "sim_01", "simulated": True}, {"external_id": OPEN_ID, "id_kind": "open"}),
    ],
    ids=["same-kind-other-id", "unknown-app-open-id", "simulated-vs-real"],
)
async def test_not_a_candidate_creates_new_viewer(store: SQLiteDatabase, first_kwargs, second_kwargs) -> None:
    viewers = store.viewers
    first = await viewers.resolve_viewer(
        platform="bilibili",
        user_name="甲",
        timestamp_ms=1_000,
        name_match_window_ms=WINDOW,
        **{"id_kind": "account", **first_kwargs},
    )
    second = await viewers.resolve_viewer(
        platform="bilibili",
        user_name="甲",
        timestamp_ms=2_000,
        name_match_window_ms=WINDOW,
        **{"id_kind": "account", **second_kwargs},
    )
    assert second.outcome == "created" and second.viewer_id != first.viewer_id


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
    # 换了开放平台应用：旧应用的 open_id 视为空着，同名时限内关联并覆盖
    new_open = "c0ffee00-0000-4000-8000-000000000000"
    moved = await _resolve(viewers, new_open, "甲", 3_000, id_kind="open", open_app_id="app2")
    assert (moved.viewer_id, moved.outcome) == (hit.viewer_id, "linked_by_name")
    row = await _row(store, hit.viewer_id)
    assert (row["open_id"], row["open_app_id"]) == (new_open, "app2")


async def test_placeholder_names_never_match(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    await _resolve(viewers, "1001", "未知用户", 1_000)
    fresh = await _resolve(viewers, OPEN_ID, "未知用户", 2_000, id_kind="open")
    assert fresh.outcome == "created"
    assert await _names(store, fresh.viewer_id) == []
    for name in ("", "?", "unknown", "Unknown", "未知用户", "用***"):
        assert is_placeholder_name(name)
    assert not is_placeholder_name("甲")


async def test_concurrent_messages_from_new_viewer_resolve_to_one_viewer(store: SQLiteDatabase) -> None:
    # 记账器并发处理同一位新观众的几条消息：只建一位观众，都归到他身上
    results = await asyncio.gather(*(_resolve(store.viewers, "3001", "并发观众", 1_000 + i) for i in range(5)))
    assert len({r.viewer_id for r in results}) == 1
    assert len(await store.execute("SELECT id FROM viewers WHERE account_id='3001'")) == 1
