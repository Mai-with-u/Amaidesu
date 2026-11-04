"""记忆账本端点：承诺增删改了结、档案行、整理状态与重新整理。"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
async def client(api, store: SQLiteDatabase):
    return await api(
        session_repo=store.sessions,
        viewer_repo=store.viewers,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
    )


async def test_read_endpoints_answer_503_when_repos_are_not_wired(api) -> None:
    """账本仓储未装配（极简启动/部分测试）时三个读端点统一 503，不返回空数据也不 500。"""
    client = await api()
    for path in ("/api/v1/memory/commitments", "/api/v1/memory/archive/1", "/api/v1/memory/settle"):
        resp = await client.get(path)
        assert resp.status_code == 503, f"{path} 应报装配缺失"
        assert resp.json()["detail"], f"{path} 的 503 没给可读文案"


async def _ended_session(store: SQLiteDatabase, *, started_at_ms: int = 1_000, kind: str = "live") -> int:
    session_id = await store.sessions.insert_live_session(started_at_ms=started_at_ms, kind=kind)
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=started_at_ms + 600)
    return session_id


async def test_commitment_list_filters_and_counts(client, store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.commitments.insert_commitment(what="接着讲鬼故事", evidence_chat_id=1, created_session_id=session_id)
    closed = await store.commitments.insert_commitment(what="已经做完的", evidence_chat_id=2)
    assert closed is not None
    await store.commitments.close_commitment(commitment_id=closed)
    voided = await store.commitments.insert_commitment(what="误记的那条", evidence_chat_id=3)
    assert voided is not None
    await store.commitments.void_commitment(commitment_id=voided)

    res = await client.get("/api/v1/memory/commitments")
    body = res.json()
    assert res.status_code == 200
    assert body["total"] == 3 and body["open_count"] == 1 and body["void_count"] == 1
    assert [item["status"] for item in body["items"]] == ["void", "closed", "open"]

    only_open = (await client.get("/api/v1/memory/commitments", params={"status": "open"})).json()
    assert only_open["total"] == 1 and only_open["items"][0]["what"] == "接着讲鬼故事"
    assert only_open["items"][0]["created_session_id"] == session_id

    bad = await client.get("/api/v1/memory/commitments", params={"status": "也许"})
    assert bad.status_code == 422


async def test_commitment_viewer_name_is_resolved(client, store: SQLiteDatabase) -> None:
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_commit_1",
        user_name="阿杰",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    await store.commitments.insert_commitment(what="给 ta 盖房子", viewer_id=viewer.viewer_id, evidence_chat_id=1)

    items = (await client.get("/api/v1/memory/commitments")).json()["items"]
    assert items[0]["viewer_id"] == viewer.viewer_id
    assert items[0]["viewer_name"] == "阿杰"


async def test_create_close_void_delete_commitment(client, store: SQLiteDatabase) -> None:
    created = await client.post("/api/v1/memory/commitments", json={"what": "手建的承诺", "quote": "我下次一定"})
    assert created.status_code == 200
    commitment_id = created.json()["id"]
    assert created.json()["status"] == "open"

    assert (await client.post(f"/api/v1/memory/commitments/{commitment_id}/close")).status_code == 200
    again: Any = await client.post(f"/api/v1/memory/commitments/{commitment_id}/close")
    assert again.status_code == 409
    rows = (await client.get("/api/v1/memory/commitments")).json()["items"]
    assert rows[0]["status"] == "closed"

    assert (await client.post(f"/api/v1/memory/commitments/{commitment_id}/void")).status_code == 200
    assert (await client.delete(f"/api/v1/memory/commitments/{commitment_id}")).status_code == 200
    assert (await client.delete(f"/api/v1/memory/commitments/{commitment_id}")).status_code == 404


async def test_archive_lines_list_and_delete_one(client, store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    first = await store.archive.insert_line(
        live_session_id=session_id, text="Minecraft：水车搭好了", quote="水车搭好啦", evidence_chat_id=5
    )
    await store.archive.insert_line(live_session_id=session_id, text="和弹幕玩接龙", quote="接个龙", evidence_chat_id=6)

    body = (await client.get(f"/api/v1/memory/archive/{session_id}")).json()
    assert body["live_session_id"] == session_id
    assert [item["text"] for item in body["items"]] == ["Minecraft：水车搭好了", "和弹幕玩接龙"]

    assert (await client.delete(f"/api/v1/memory/archive/lines/{first}")).status_code == 200
    remaining = (await client.get(f"/api/v1/memory/archive/{session_id}")).json()["items"]
    assert [item["text"] for item in remaining] == ["和弹幕玩接龙"]
    assert (await client.delete(f"/api/v1/memory/archive/lines/{first}")).status_code == 404


async def test_settle_state_exposes_pending_report_and_resettle(client, store: SQLiteDatabase) -> None:
    session_id = await _ended_session(store)
    await store.archive.insert_line(live_session_id=session_id, text="档案", quote="档案", evidence_chat_id=1)
    await store.sessions.record_settle_failure(
        live_session_id=session_id, settle_report='{"failure_reason": "模型调用失败"}'
    )

    body = (await client.get("/api/v1/memory/settle")).json()
    assert body["pending_count"] == 1 and body["max_attempts"] == 3
    item = body["items"][0]
    assert item["live_session_id"] == session_id
    assert item["settle_attempts"] == 1
    assert item["settle_report"]["failure_reason"] == "模型调用失败"
    assert item["archive_count"] == 1

    assert (await client.post(f"/api/v1/memory/settle/{session_id}/resettle")).status_code == 200
    after = (await client.get("/api/v1/memory/settle")).json()["items"][0]
    assert after["settle_attempts"] == 0 and after["settle_report"] is None
    assert (await client.post("/api/v1/memory/settle/9999/resettle")).status_code == 404
