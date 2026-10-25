"""观众身份人工处理端点：待确认列表、同意并入 / 改选、保持新建、取消关联，真实临时库经完整 HTTP 层。"""

from __future__ import annotations

import pytest

from src.modules.storage.database import SQLiteDatabase

DAY = 86_400_000
WINDOW = 90 * DAY
OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"


@pytest.fixture
async def client(api, store: SQLiteDatabase, event_bus):
    return await api(
        event_bus=event_bus,
        viewer_repo=store.viewers,
        chat_repo=store.chat,
        viewer_identity_repo=store.viewer_identity,
    )


async def _resolve(store: SQLiteDatabase, external_id: str, name: str, ts: int, kind: str = "account") -> int:
    result = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind=kind,  # type: ignore[arg-type]
        external_id=external_id,
        user_name=name,
        timestamp_ms=ts,
        name_match_window_ms=WINDOW,
        open_app_id="app1" if kind == "open" else "",
    )
    return result.viewer_id


async def test_pending_list_and_agree_merge(client, store) -> None:
    old = await _resolve(store, "1001", "甲", 1_000)
    pending = await _resolve(store, OPEN_ID, "甲", 1_000 + WINDOW + 1, kind="open")

    body = (await client.get("/api/v1/viewers/pending")).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert (item["viewer_id"], item["open_id"], item["candidate"]) == (pending, OPEN_ID, {"viewer_id": old, "user_name": "甲"})
    assert (item["candidate_account_id"], item["candidate_names"]) == ("1001", ["甲"])
    detail = (await client.get(f"/api/v1/viewers/{pending}")).json()
    assert (detail["link_status"], detail["link_candidate"]) == ("pending", {"viewer_id": old, "user_name": "甲"})

    # 同意：并入建议的老观众，返回老观众 ID；来源观众不再存在
    res = await client.post(f"/api/v1/viewers/{pending}/merge", json={"target_viewer_id": old})
    assert (res.status_code, res.json()) == (200, {"viewer_id": old})
    assert (await client.get(f"/api/v1/viewers/{pending}")).status_code == 404
    merged = (await client.get(f"/api/v1/viewers/{old}")).json()
    assert (merged["account_id"], merged["open_id"]) == ("1001", OPEN_ID)
    assert (await client.get("/api/v1/viewers/pending")).json()["total"] == 0


async def test_keep_unlink_and_refusals(client, store) -> None:
    old = await _resolve(store, "1001", "甲", 1_000)
    pending = await _resolve(store, OPEN_ID, "甲", 1_000 + WINDOW + 1, kind="open")

    assert (await client.post(f"/api/v1/viewers/{pending}/keep")).json() == {"viewer_id": pending}
    assert (await client.post(f"/api/v1/viewers/{pending}/keep")).status_code == 409  # 已不在待确认

    other = await _resolve(store, "1002", "乙", 2_000)
    refused = await client.post(f"/api/v1/viewers/{other}/merge", json={"target_viewer_id": old})
    assert refused.status_code == 409 and "两个不同账号" in refused.json()["detail"]

    # 手动合并后再拆开：拆出的新观众带回开放平台 ID
    assert (await client.post(f"/api/v1/viewers/{pending}/merge", json={"target_viewer_id": old})).status_code == 200
    res = await client.post(f"/api/v1/viewers/{old}/unlink", json={"id_kind": "open"})
    assert res.status_code == 200
    split = (await client.get(f"/api/v1/viewers/{res.json()['viewer_id']}")).json()
    assert (split["open_id"], split["account_id"]) == (OPEN_ID, None)
    last = await client.post(f"/api/v1/viewers/{old}/unlink", json={"id_kind": "account"})
    assert last.status_code == 409 and "只剩这一个 ID" in last.json()["detail"]
    assert (await client.post(f"/api/v1/viewers/{old}/unlink", json={"id_kind": "other"})).status_code == 422


async def test_identity_endpoints_require_repo(api, event_bus) -> None:
    client = await api(event_bus=event_bus)
    assert (await client.get("/api/v1/viewers/pending")).status_code == 503
    assert (await client.post("/api/v1/viewers/1/keep")).status_code == 503
