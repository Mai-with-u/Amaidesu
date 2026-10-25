"""Viewers API：观众域只读端点（列表 / 分析 / 档案 / 对话 / 贡献 / 场次），真实临时库经完整 HTTP 层"""

from __future__ import annotations

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.utils.time_utils import now_ms
from tests.modules.storage.viewer_seed import seed_gift, seed_message, seed_replied

BASE = 1_700_000_000_000
DAY = 86_400_000


@pytest.fixture
async def viewers_client(api, store: SQLiteDatabase, event_bus):
    return await api(event_bus=event_bus, viewer_repo=store.viewers, chat_repo=store.chat)


async def _message(store: SQLiteDatabase, user_id: str, name: str, ts: int) -> None:
    await seed_message(store.viewers, platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts)


async def _chat(store: SQLiteDatabase, ts: int, session: int = 1, user: str = "u_full", **kwargs) -> None:
    kwargs.setdefault("sender_role", "viewer")
    kwargs.setdefault("message_type", "danmaku")
    await store.chat.insert_live_chat(
        live_session_id=session, timestamp_ms=ts, sender_id=user, sender_name="富观众", **kwargs
    )


async def _seed_full_viewer(store: SQLiteDatabase) -> None:
    """一个完整观众：场次 1 两条发言 + 礼物，场次 2 一条主播回复 + SC。"""
    await _message(store, "u_full", "富观众", BASE + 5_000)
    await seed_replied(store.viewers, platform="bilibili", user_id="u_full", timestamp_ms=BASE + 5_500)
    await _chat(store, BASE, content="第一条", message_id="fm1")
    await _chat(store, BASE + 1_000, content="第二条", message_id="fm2")
    await _chat(
        store, BASE + 9_000, session=2, user=None, sender_role="assistant", content="谢谢支持",
        message_type="speech", reply_to_message_id="fm2",
    )  # fmt: skip
    await store.chat.insert_gift(
        live_session_id=1, timestamp_ms=BASE + 2_000, user_id="u_full", user_name="富观众",
        gift_name="小心心", quantity=2, currency="bilibili_gold_coin",
    )  # fmt: skip
    await store.chat.insert_super_chat(
        live_session_id=2, timestamp_ms=BASE + 8_000, user_id="u_full", user_name="富观众",
        total_price=30_000, currency="bilibili_gold_coin", message="加油",
    )  # fmt: skip


async def test_list_fields_order_search_and_paging(viewers_client, store) -> None:
    client = viewers_client
    empty = (await client.get("/api/v1/viewers")).json()
    assert (empty["total"], empty["items"]) == (0, [])

    for i in range(5):
        await _message(store, f"u{i}", f"观众{i}", BASE + i)
    await seed_gift(store.viewers, platform="bilibili", user_id="u0", user_name="观众0", timestamp_ms=BASE + 1_000)

    body = (await client.get("/api/v1/viewers")).json()
    assert body["total"] == 5
    assert set(body["items"][0]) == {
        "platform", "user_id", "user_name", "message_count", "gift_count", "replied_count",
        "interaction_count", "paid_count", "paid_amount", "last_active_ms",
    }  # fmt: skip

    top = (await client.get("/api/v1/viewers", params={"order_by": "gift_count", "limit": 2})).json()["items"][0]
    assert (top["user_id"], top["gift_count"]) == ("u0", 1)
    found = (await client.get("/api/v1/viewers", params={"search": "观众3"})).json()
    assert (found["total"], found["items"][0]["user_id"]) == (1, "u3")
    paged = (await client.get("/api/v1/viewers", params={"limit": 2, "offset": 4})).json()
    assert (paged["total"], len(paged["items"])) == (5, 1)  # total 不随分页变
    bad = await client.get("/api/v1/viewers", params={"order_by": "user_id; DROP TABLE viewers"})
    assert bad.status_code == 400


async def test_viewer_detail_aggregates_across_tables(viewers_client, store) -> None:
    await _seed_full_viewer(store)
    body = (await viewers_client.get("/api/v1/viewers/u_full")).json()
    assert (body["user_name"], body["message_count"], body["replied_count"]) == ("富观众", 1, 1)
    assert body["first_seen_ms"] == BASE  # 三表最早（发言）
    assert (body["gift_total_count"], body["sc_total_amount"], body["sc_total_count"]) == (2, 30.0, 1)
    # 只按观众发言行聚合：发言都在场次 1，场次 2 仅主播回复行
    assert body["session_count"] == 1
    assert (await viewers_client.get("/api/v1/viewers/ghost")).status_code == 404


async def test_viewer_dialogue_interleaves_and_pages(viewers_client, store) -> None:
    await _seed_full_viewer(store)
    first = (await viewers_client.get("/api/v1/viewers/u_full/messages", params={"limit": 1})).json()
    assert [i["message_id"] for i in first["items"] if i["kind"] == "viewer"] == ["fm2"]
    assert first["next_before"] == BASE + 1_000

    full = (await viewers_client.get("/api/v1/viewers/u_full/messages")).json()
    assert [(i["kind"], i["timestamp_ms"]) for i in full["items"]] == [
        ("viewer", BASE),
        ("viewer", BASE + 1_000),
        ("reply", BASE + 9_000),
    ]
    assert full["next_before"] is None  # 观众消息不足 limit，翻尽

    older = (
        await viewers_client.get("/api/v1/viewers/u_full/messages", params={"before_timestamp_ms": BASE + 1_000})
    ).json()
    assert [i["message_id"] for i in older["items"]] == ["fm1"]


async def test_viewer_contributions_and_sessions(viewers_client, store) -> None:
    await _seed_full_viewer(store)
    body = (await viewers_client.get("/api/v1/viewers/u_full/contributions")).json()
    assert (body["gift_total_count"], body["sc_total_amount"], body["sc_total_count"]) == (2, 30.0, 1)
    assert (body["gifts"][0]["gift_name"], body["super_chats"][0]["message"]) == ("小心心", "加油")

    sessions = (await viewers_client.get("/api/v1/viewers/u_full/sessions")).json()["items"]
    assert [(s["live_session_id"], s["message_count"]) for s in sessions] == [(1, 2)]


async def test_insights_buckets_and_daily_danmaku(viewers_client, store) -> None:
    now = now_ms()
    await _message(store, "a_today", "今日观众", now)
    await _message(store, "b_week", "本周观众", now - 3 * DAY)
    await _message(store, "c_old", "老观众", now - 60 * DAY)
    await _chat(store, now, user="a_today", content="今天第一条")
    await _chat(store, now - 1_000, user="a_today", content="今天第二条")

    body = (await viewers_client.get("/api/v1/viewers/insights")).json()
    assert (body["total_viewers"], body["active_today"], body["active_week"]) == (3, 1, 1)
    assert (body["active_month"], body["active_older"], body["never_replied"], body["gift_viewers"]) == (0, 1, 3, 0)
    assert [d["count"] for d in body["daily_danmaku"]] == [2]


async def test_endpoints_require_repos(api, event_bus) -> None:
    """观众仓储未装配（server 极简启动形态）→ 503。"""
    client = await api(event_bus=event_bus)
    assert (await client.get("/api/v1/viewers")).status_code == 503
    assert (await client.get("/api/v1/viewers/insights")).status_code == 503
