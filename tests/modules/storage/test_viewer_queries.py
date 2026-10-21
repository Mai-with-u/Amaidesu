"""观众域查询：列表搜索分页、活跃分桶、对话交织、活动边界、贡献、场次与日弹幕统计"""

from __future__ import annotations

from typing import Any

from src.modules.storage.database import SQLiteDatabase
from src.modules.utils.time_utils import now_ms

PLATFORM = "bilibili"
BASE = 1_700_000_000_000
DAY = 86_400_000


async def _seed_viewer(store: SQLiteDatabase, user_id: str, user_name: str, last_active_ms: int | None = None) -> None:
    ts = last_active_ms if last_active_ms is not None else now_ms()
    await store.viewers.upsert_viewer_message(platform=PLATFORM, user_id=user_id, user_name=user_name, timestamp_ms=ts)


async def _chat(store: SQLiteDatabase, ts: int, sender_id: str | None = "u_d", **kwargs: Any) -> None:
    kwargs.setdefault("live_session_id", 1)
    kwargs.setdefault("sender_role", "viewer")
    kwargs.setdefault("content", "发言")
    kwargs.setdefault("message_type", "danmaku")
    await store.chat.insert_live_chat(timestamp_ms=ts, sender_id=sender_id, **kwargs)


async def _gift(store: SQLiteDatabase, ts: int, user_id: str, quantity: int, total_price: int) -> None:
    await store.chat.insert_gift(
        live_session_id=1,
        timestamp_ms=ts,
        platform=PLATFORM,
        user_id=user_id,
        user_name="礼官",
        gift_name="小心心",
        quantity=quantity,
        total_price=total_price,
        currency="bilibili_gold_coin",
    )


async def _sc(store: SQLiteDatabase, ts: int, user_id: str, total_price: int) -> None:
    await store.chat.insert_super_chat(
        live_session_id=2,
        timestamp_ms=ts,
        platform=PLATFORM,
        user_id=user_id,
        user_name="礼官",
        total_price=total_price,
        currency="bilibili_gold_coin",
        message="SC",
    )


async def test_list_search_and_pagination(store: SQLiteDatabase) -> None:
    for i in range(4):
        await _seed_viewer(store, f"vip{i}", f"贵宾{i}")
    await _seed_viewer(store, "u_", "带下划线的名字")

    async def ids(**kwargs) -> tuple[list[str], int]:
        rows, total = await store.viewers.list_viewer_stats(**kwargs)
        return sorted(r["user_id"] for r in rows), total

    assert await ids(search="贵宾2") == (["vip2"], 1)
    assert await ids(search="vip3") == (["vip3"], 1)
    assert await ids(search="不存在的名字") == ([], 0)
    # LIKE 元字符按字面匹配，"_" 不当单字符通配
    assert await ids(search="u_") == (["u_"], 1)
    # total 与分页解耦；越界 offset 不报错
    assert len((await ids(limit=2, offset=4))[0]) == 1 and (await ids(limit=2))[1] == 5
    assert await ids(limit=2, offset=100) == ([], 5)
    rows, total = await ids(search="vip", limit=2, offset=2)
    assert (len(rows), total) == (2, 4)


async def test_insight_buckets_are_mutually_exclusive(store: SQLiteDatabase) -> None:
    empty = await store.viewers.viewer_insight_buckets()
    assert set(empty.values()) == {0}

    now = now_ms()
    # 以 now 为锚错开数量级，避开本地时区零点临界
    for user_id, age in (("u_today", 0), ("u_week", 3 * DAY), ("u_month", 20 * DAY), ("u_older", 60 * DAY)):
        await _seed_viewer(store, user_id, user_id, last_active_ms=now - age)
    await store.viewers.upsert_viewer_gift(platform=PLATFORM, user_id="u_today", user_name="今天", timestamp_ms=now)

    assert await store.viewers.viewer_insight_buckets() == {
        "total": 4,
        "active_today": 1,
        "active_week": 1,
        "active_month": 1,
        "active_older": 1,
        "never_replied": 4,
        "gift_viewers": 1,
    }


async def test_dialogue_interleaves_own_replies_with_cursor(store: SQLiteDatabase) -> None:
    for i, mid in enumerate(("m1", "m2", "m3")):
        await _chat(store, BASE + i * 1000, message_id=mid, content=f"消息{mid}")
    await _chat(store, BASE + 1500, None, sender_role="assistant", message_type="speech", reply_to_message_id="m2")
    # 回复他人消息的 assistant 行不混入
    await _chat(store, BASE + 100, "u_other", message_id="others")
    await _chat(store, BASE + 200, None, sender_role="assistant", message_type="speech", reply_to_message_id="others")

    rows = await store.chat.list_user_dialogue(user_id="u_d", limit=10)
    assert [(r["sender_role"], r["timestamp_ms"]) for r in rows] == [
        ("viewer", BASE),
        ("viewer", BASE + 1000),
        ("assistant", BASE + 1500),
        ("viewer", BASE + 2000),
    ]
    # 首批取最近 2 条观众消息并交织回复；游标翻更早只剩 m1
    batch = await store.chat.list_user_dialogue(user_id="u_d", limit=2)
    assert [r["message_id"] for r in batch if r["sender_role"] == "viewer"] == ["m2", "m3"]
    assert any(r["sender_role"] == "assistant" for r in batch)
    batch = await store.chat.list_user_dialogue(user_id="u_d", limit=2, before_timestamp_ms=BASE + 1000)
    assert [r["message_id"] for r in batch] == ["m1"]


async def test_activity_bounds_span_chat_gifts_and_super_chats(store: SQLiteDatabase) -> None:
    assert await store.chat.get_user_activity_bounds(user_id="u_b") is None
    await _chat(store, BASE + 5_000, "u_b")
    await _gift(store, BASE, "u_b", 1, 1000)
    await _sc(store, BASE + 9_000, "u_b", 30_000)
    assert await store.chat.get_user_activity_bounds(user_id="u_b") == (BASE, BASE + 9_000)


async def test_user_contributions_detail_and_summary(store: SQLiteDatabase) -> None:
    assert await store.chat.summarize_user_contributions(user_id="u_c") == {
        "gift_total_count": 0,
        "gift_total_amount": 0,
        "sc_total_amount": 0,
        "sc_total_count": 0,
    }
    assert await store.chat.list_user_gifts(user_id="u_c") == []

    await _gift(store, BASE, "u_c", 2, 2000)
    await _gift(store, BASE + 1_000, "u_c", 3, 300)
    await _sc(store, BASE + 2_000, "u_c", 50_000)

    assert [r["quantity"] for r in await store.chat.list_user_gifts(user_id="u_c")] == [3, 2]  # 时间倒序
    assert len(await store.chat.list_user_super_chats(user_id="u_c")) == 1
    summary = await store.chat.summarize_user_contributions(user_id="u_c")
    assert (summary["gift_total_count"], summary["sc_total_amount"], summary["sc_total_count"]) == (5, 50_000, 1)


async def test_user_sessions_grouped_with_optional_title(store: SQLiteDatabase) -> None:
    pk = await store.sessions.insert_live_session(started_at_ms=BASE + DAY, title="周六场")
    # 场次 101 不存在：LEFT JOIN 容忍 NULL 标题
    await _chat(store, BASE, "u_s", live_session_id=101)
    await _chat(store, BASE + DAY, "u_s", live_session_id=pk)

    rows = await store.chat.list_user_sessions(user_id="u_s")
    assert [(r["live_session_id"], r["title"], r["message_count"]) for r in rows] == [(pk, "周六场", 1), (101, None, 1)]


async def test_daily_danmaku_counts_only_danmaku_type(store: SQLiteDatabase) -> None:
    now = now_ms()
    await _chat(store, now, "u1")
    await _chat(store, now - 1_000, "u2")
    await _chat(store, now, "u3", message_type="guard")

    for days in (7, 1):
        rows = await store.chat.daily_danmaku_counts(days=days)
        assert [r["count"] for r in rows] == [2]
