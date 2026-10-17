"""Memory API：观众画像管理端点（画像列表 / 纠正 / 删除 / 统计），真实临时库的 ``ViewerProfileRepo`` 经完整 HTTP 层。

造数直连仓储写入方法（与主播 Agent 收播写库同链路）。
"""

from __future__ import annotations

import pytest

from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
async def memory_client(api, store: SQLiteDatabase, event_bus):
    return await api(event_bus=event_bus, viewer_profile_repo=store.viewer_profiles)


async def _seed_profiles(store: SQLiteDatabase, count: int) -> None:
    """user_id 递增，文本含可搜索关键词。"""
    for i in range(count):
        text = f"印象{i}：喜欢Minecraft" if i % 2 else f"印象{i}：喜欢恐怖游戏"
        await store.viewer_profiles.upsert_profile(
            platform="bilibili", user_id=f"u_{i}", profile_text=text, last_compressed_at_ms=1_000 + i
        )


async def test_roster_list_shape_search_and_paging(memory_client, store) -> None:
    assert (await memory_client.get("/api/v1/memory/profiles")).json() == {"total": 0, "items": []}

    await _seed_profiles(store, 5)
    body = (await memory_client.get("/api/v1/memory/profiles")).json()
    assert set(body["items"][0]) == {"platform", "user_id", "profile_text", "last_compressed_at_ms", "updated_at_ms"}
    assert body["total"] == 5

    found = (await memory_client.get("/api/v1/memory/profiles", params={"search": "Minecraft"})).json()
    assert found["total"] == 2 and all("Minecraft" in item["profile_text"] for item in found["items"])

    pages = [
        (await memory_client.get("/api/v1/memory/profiles", params={"limit": 2, "offset": offset})).json()
        for offset in (0, 2)
    ]
    assert all(page["total"] == 5 and len(page["items"]) == 2 for page in pages)
    assert {i["user_id"] for i in pages[0]["items"]}.isdisjoint({i["user_id"] for i in pages[1]["items"]})


async def test_roster_entry_update_and_delete(memory_client, store) -> None:
    await _seed_profiles(store, 1)
    url = "/api/v1/memory/profiles/bilibili/u_0"

    res = await memory_client.patch(url, json={"profile_text": "人工纠正后的印象"})
    assert (res.status_code, res.json()) == (200, {"success": True})
    assert (await memory_client.get("/api/v1/memory/profiles", params={"search": "人工纠正"})).json()["total"] == 1
    assert (await memory_client.patch(url, json={"profile_text": "   "})).status_code == 422
    missing = await memory_client.patch("/api/v1/memory/profiles/bilibili/nobody", json={"profile_text": "x"})
    assert missing.status_code == 404

    assert (await memory_client.delete(url)).json() == {"success": True}
    assert (await memory_client.delete(url)).status_code == 404
    assert (await memory_client.get("/api/v1/memory/profiles")).json()["total"] == 0


async def test_stats_and_retired_fact_endpoints(memory_client, store) -> None:
    assert (await memory_client.get("/api/v1/memory/stats")).json() == {"profile_count": 0, "latest_updated_ms": 0}
    await _seed_profiles(store, 2)
    body = (await memory_client.get("/api/v1/memory/stats")).json()
    assert body["profile_count"] == 2 and body["latest_updated_ms"] > 0

    # 观众事实中间层已退役：事实端点不再存在
    assert (await memory_client.get("/api/v1/memory/facts", params={"search": "x"})).status_code == 404


async def test_endpoints_503_when_the_roster_is_not_wired(api, event_bus) -> None:
    client = await api(event_bus=event_bus, viewer_profile_repo=None)

    assert (await client.get("/api/v1/memory/profiles")).status_code == 503
    assert (await client.get("/api/v1/memory/stats")).status_code == 503
