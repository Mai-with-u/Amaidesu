"""Viewers API 契约 smoke：路由、状态码与 JSON 形状（真实临时库经完整 HTTP 层）。

装配逻辑的取值断言在 ``tests/modules/dashboard/services/test_viewer_profiles.py``
（内存假仓储直测服务层）；这里只守住 HTTP 契约——路由可达、状态码映射
（200 / 400 / 404 / 503）与响应键集。
"""

from __future__ import annotations

import pytest

from src.modules.storage.database import SQLiteDatabase
from tests.modules.storage.viewer_seed import seed_gift, seed_message

BASE = 1_700_000_000_000

LIST_ITEM_KEYS = {
    "viewer_id", "platform", "user_id", "user_name", "link_status", "message_count", "gift_count",
    "replied_count", "interaction_count", "paid_count", "paid_amount", "last_active_ms",
}  # fmt: skip


@pytest.fixture
async def viewers_client(api, store: SQLiteDatabase, event_bus):
    return await api(event_bus=event_bus, viewer_repo=store.viewers, chat_repo=store.chat)


async def _message(store: SQLiteDatabase, user_id: str, name: str, ts: int) -> int:
    return await seed_message(store.viewers, platform="bilibili", user_id=user_id, user_name=name, timestamp_ms=ts)


async def test_list_contract_keys_filter_and_paging(viewers_client, store) -> None:
    empty = (await viewers_client.get("/api/v1/viewers")).json()
    assert (empty["total"], empty["items"]) == (0, [])

    vid = await _message(store, "u0", "观众0", BASE)
    await seed_gift(store.viewers, platform="bilibili", user_id="u0", user_name="观众0", timestamp_ms=BASE + 1_000)

    body = (await viewers_client.get("/api/v1/viewers")).json()
    assert body["total"] == 1
    assert set(body["items"][0]) == LIST_ITEM_KEYS
    assert (body["items"][0]["viewer_id"], body["items"][0]["gift_count"]) == (vid, 1)
    paged = (await viewers_client.get("/api/v1/viewers", params={"limit": 1, "offset": 1})).json()
    assert (paged["total"], paged["items"]) == (1, [])  # total 不随分页变


async def test_list_rejects_illegal_order_by_with_400(viewers_client) -> None:
    bad = await viewers_client.get("/api/v1/viewers", params={"order_by": "user_id; DROP TABLE viewers"})
    assert bad.status_code == 400


async def test_detail_contract_and_404(viewers_client, store) -> None:
    vid = await _message(store, "u0", "观众0", BASE)
    body = (await viewers_client.get(f"/api/v1/viewers/{vid}")).json()
    assert (body["viewer_id"], body["user_id"], body["link_status"]) == (vid, "u0", "confirmed")
    assert (await viewers_client.get("/api/v1/viewers/99999")).status_code == 404


async def test_read_subresource_routes_are_reachable(viewers_client, store) -> None:
    """对话 / 贡献 / 场次 / 分析四个只读子路由的形状 smoke（取值断言在服务层单测）。"""
    vid = await _message(store, "u0", "观众0", BASE)
    messages = (await viewers_client.get(f"/api/v1/viewers/{vid}/messages")).json()
    assert set(messages) == {"items", "next_before"}
    contributions = (await viewers_client.get(f"/api/v1/viewers/{vid}/contributions")).json()
    assert set(contributions) == {"gift_total_count", "sc_total_amount", "sc_total_count", "gifts", "super_chats"}
    sessions = (await viewers_client.get(f"/api/v1/viewers/{vid}/sessions")).json()
    assert set(sessions) == {"items"}
    insights = (await viewers_client.get("/api/v1/viewers/insights", params={"days": 7})).json()
    assert set(insights) == {
        "total_viewers", "active_today", "active_week", "active_month", "active_older",
        "never_replied", "gift_viewers", "daily_danmaku",
    }  # fmt: skip


async def test_endpoints_require_repos(api, event_bus) -> None:
    """观众仓储未装配（server 极简启动形态）→ 503。"""
    client = await api(event_bus=event_bus)
    assert (await client.get("/api/v1/viewers")).status_code == 503
    assert (await client.get("/api/v1/viewers/insights")).status_code == 503
