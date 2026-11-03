"""观众隐私端点：删除观众、不记录名单、撤回待执行删除，真实临时库经完整 HTTP 层。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.privacy import GRACE_DELETE_MAX_MS, PrivacyService


@pytest.fixture
async def privacy(store: SQLiteDatabase, tmp_path: Path) -> PrivacyService:
    service = PrivacyService(store.manager, db_path=store.db_path, key_path=tmp_path / "forget.key", bot_name="麦麦")
    await service.initialize()
    yield service
    await service.close()


@pytest.fixture
async def client(api, store: SQLiteDatabase, event_bus, privacy: PrivacyService):
    return await api(
        event_bus=event_bus,
        viewer_repo=store.viewers,
        chat_repo=store.chat,
        viewer_identity_repo=store.viewer_identity,
        privacy_service=privacy,
    )


async def _viewer(store: SQLiteDatabase, external_id: str, name: str, ts: int = 1_000) -> int:
    result = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id=external_id,
        user_name=name,
        timestamp_ms=ts,
        name_match_window_ms=0,
    )
    return result.viewer_id


async def test_delete_viewer_with_forget_list_and_unblock(
    client, store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    vid = await _viewer(store, "u_del_1", "待删观众")

    res = await client.delete(f"/api/v1/viewers/{vid}", params={"add_to_forget_list": True})
    assert (res.status_code, res.json()) == (200, {"deleted": True, "added_to_forget_list": True})
    assert (await client.get(f"/api/v1/viewers/{vid}")).status_code == 404
    assert (await client.get("/api/v1/viewers/forget-list")).json() == {"count": 1}
    assert (await client.delete(f"/api/v1/viewers/{vid}")).status_code == 404

    miss = await client.post("/api/v1/viewers/forget-list/remove", json={"id_kind": "account", "external_id": "nobody"})
    assert miss.json() == {"removed": False, "count": 1}
    hit = await client.post("/api/v1/viewers/forget-list/remove", json={"id_kind": "account", "external_id": "u_del_1"})
    assert hit.json() == {"removed": True, "count": 0}
    assert await privacy.is_external_id_forgotten(id_kind="account", external_id="u_del_1") is False


async def test_delete_without_forget_list_keeps_list_empty(client, store: SQLiteDatabase) -> None:
    vid = await _viewer(store, "u_del_2", "普通删除")
    res = await client.delete(f"/api/v1/viewers/{vid}")
    assert res.json() == {"deleted": True, "added_to_forget_list": False}
    assert (await client.get("/api/v1/viewers/forget-list")).json() == {"count": 0}


async def test_retract_pending_forget_and_detail_exposes_deadline(
    client, store: SQLiteDatabase, privacy: PrivacyService
) -> None:
    vid = await _viewer(store, "u_pending", "待删除者")
    await privacy.process_incoming_viewer_message(
        platform="bilibili",
        id_kind="account",
        external_id="u_pending",
        user_name="待删除者",
        message_type="danmaku",
        content="忘了我",
        timestamp_ms=5_000,
    )

    detail = (await client.get(f"/api/v1/viewers/{vid}")).json()
    assert detail["forget_requested_at_ms"] is not None
    assert detail["forget_due_at_ms"] == detail["forget_requested_at_ms"] + GRACE_DELETE_MAX_MS
    listed = (await client.get("/api/v1/viewers")).json()["items"]
    assert [item["forget_requested_at_ms"] is not None for item in listed] == [True]

    res = await client.post(f"/api/v1/viewers/{vid}/retract-forget")
    assert (res.status_code, res.json()) == (200, {"viewer_id": vid})
    assert (await client.get(f"/api/v1/viewers/{vid}")).json()["forget_requested_at_ms"] is None
    assert (await client.get("/api/v1/viewers/forget-list")).json() == {"count": 0}
    assert (await client.post(f"/api/v1/viewers/{vid}/retract-forget")).status_code == 409


async def test_privacy_endpoints_require_service(api, event_bus, store: SQLiteDatabase) -> None:
    client = await api(event_bus=event_bus, viewer_repo=store.viewers, chat_repo=store.chat)
    assert (await client.get("/api/v1/viewers/forget-list")).status_code == 503
    assert (await client.delete("/api/v1/viewers/1")).status_code == 503
    assert (await client.post("/api/v1/viewers/1/retract-forget")).status_code == 503
