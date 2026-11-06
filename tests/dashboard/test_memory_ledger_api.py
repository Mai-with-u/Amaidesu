"""记忆账本端点：承诺增删改了结、档案行、整理状态与重新整理。"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.memory import KeptSnapshotLine, PendingArchiveLine, PendingViewerCandidate
from src.modules.storage.repos.viewer_candidates import replace_session_candidates


@pytest.fixture
async def client(api, store: SQLiteDatabase):
    return await api(
        session_repo=store.sessions,
        viewer_repo=store.viewers,
        commitments_repo=store.commitments,
        archive_repo=store.archive,
        memory_review_repo=store.memory_review,
        viewer_candidate_repo=store.viewer_candidates,
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


async def test_resettle_rejects_rehearsal_and_ongoing(client, store: SQLiteDatabase) -> None:
    """彩排场从不整理、进行中的场次还没内容可整理：都返回 409 与可读原因。"""
    rehearsal = await _ended_session(store, started_at_ms=1_000, kind="rehearsal")
    rehearsal_resp = await client.post(f"/api/v1/memory/settle/{rehearsal}/resettle")
    assert rehearsal_resp.status_code == 409
    assert "彩排" in rehearsal_resp.json()["detail"]

    ongoing = await store.sessions.insert_live_session(started_at_ms=2_000)
    ongoing_resp = await client.post(f"/api/v1/memory/settle/{ongoing}/resettle")
    assert ongoing_resp.status_code == 409
    assert "还没结束" in ongoing_resp.json()["detail"]


async def test_commitments_and_archive_lines_carry_game_and_save(client, store: SQLiteDatabase) -> None:
    """挂了游戏的承诺与档案行带上存档编号与名字，记忆页据此标出是哪个存档里的事。"""
    session_id = await _ended_session(store)
    await store.commitments.insert_commitment(
        what="去村里换绿宝石", evidence_chat_id=1, game="minecraft", save_id="b", save_name="新档"
    )
    await store.archive.insert_line(
        live_session_id=session_id,
        text="Minecraft：出生点旁边有村子",
        quote="出生点旁边就有村子",
        evidence_chat_id=2,
        game="minecraft",
        save_id="b",
        save_name="新档",
    )
    await store.archive.insert_line(live_session_id=session_id, text="猜谜环节", quote="猜得好准", evidence_chat_id=3)

    item = (await client.get("/api/v1/memory/commitments")).json()["items"][0]
    assert (item["game"], item["save_id"], item["save_name"]) == ("minecraft", "b", "新档")
    lines = (await client.get(f"/api/v1/memory/archive/{session_id}")).json()["items"]
    assert [(line["game"], line["save_name"]) for line in lines] == [("minecraft", "新档"), (None, "")]


async def _settled(store: SQLiteDatabase, session_id: int, new: list, kept: list | None = None) -> None:
    await store.sessions.apply_settlement(
        live_session_id=session_id,
        archive_lines=new,
        commitments=[],
        closures=[],
        settled_at_ms=9_999,
        now_ms_value=9_999,
        snapshot_kept=kept or [],
    )


async def test_review_actions_lock_pin_add_and_mark_reviewed(client, store: SQLiteDatabase) -> None:
    """记忆页审核：列表标出每行在不在最新快照里；改标题、标稳定、放回、新增、标已审都经接口落库。"""
    first = await _ended_session(store, started_at_ms=1_000)
    await _settled(
        store,
        first,
        [
            PendingArchiveLine(text="给狼取名阿黄", quote="就叫阿黄", evidence_chat_id=1, lasting=True),
            PendingArchiveLine(text="挖到铁矿", quote="铁矿来了", evidence_chat_id=2),
        ],
    )
    kept_id, dropped_id = [line.id for line in await store.archive.list_lines(live_session_id=first)]
    second = await _ended_session(store, started_at_ms=2_000_000)
    await _settled(store, second, [], [KeptSnapshotLine(archive_line_id=kept_id, lasting=True, seen_at_ms=1_000)])

    body = (await client.get(f"/api/v1/memory/archive/{first}")).json()
    assert (body["snapshot_session_id"], body["settled"], body["reviewed_at_ms"]) == (second, True, None)
    assert [(i["in_snapshot"], i["lasting"], i["pinned"], i["human_locked"]) for i in body["items"]] == [
        (True, True, False, False),
        (False, False, False, False),
    ]

    assert (
        await client.put(f"/api/v1/memory/archive/lines/{kept_id}/title", json={"text": "狼叫阿黄"})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/memory/archive/lines/{kept_id}/lasting", json={"lasting": False})
    ).status_code == 200
    assert (await client.post(f"/api/v1/memory/archive/lines/{dropped_id}/pin")).status_code == 200
    body = (await client.get(f"/api/v1/memory/archive/{first}")).json()
    assert [(i["text"], i["in_snapshot"], i["lasting"], i["pinned"], i["human_locked"]) for i in body["items"]] == [
        ("狼叫阿黄", True, False, False, True),
        ("挖到铁矿", True, False, True, False),
    ]
    assert body["snapshot_pinned_count"] == 1

    said = await store.chat.insert_live_chat(
        live_session_id=second, timestamp_ms=5, sender_role="assistant", content="猫就叫团子吧", message_type="text"
    )
    wrong = await client.post(
        f"/api/v1/memory/archive/{second}/lines", json={"evidence_chat_id": said, "quote": "狗", "text": "猫叫团子"}
    )
    assert wrong.status_code == 422 and "逐字" in wrong.json()["detail"]
    added = await client.post(
        f"/api/v1/memory/archive/{second}/lines",
        json={"evidence_chat_id": said, "quote": "猫就叫团子", "text": "猫叫团子", "lasting": True},
    )
    assert added.status_code == 200
    again = await client.post(f"/api/v1/memory/archive/lines/{dropped_id}/pin")
    assert again.status_code == 409

    settle = (await client.get("/api/v1/memory/settle")).json()
    assert settle["review_pending_count"] == 2
    assert (await client.post(f"/api/v1/memory/settle/{second}/reviewed")).status_code == 200
    settle = (await client.get("/api/v1/memory/settle")).json()
    assert settle["review_pending_count"] == 1
    assert [item["reviewed_at_ms"] is not None for item in settle["items"]] == [True, False]
    assert (await client.post("/api/v1/memory/settle/9999/reviewed")).status_code == 404


async def test_resettle_refuses_a_session_with_human_edits(client, store: SQLiteDatabase) -> None:
    """整场替换会冲掉人工改动：这一场有人改过的行时重新整理返回 409 与原因。"""
    session_id = await _ended_session(store)
    await _settled(store, session_id, [PendingArchiveLine(text="给狼取名", quote="就叫阿黄", evidence_chat_id=1)])
    line_id = (await store.archive.list_lines(live_session_id=session_id))[0].id
    assert (
        await client.put(f"/api/v1/memory/archive/lines/{line_id}/title", json={"text": "狼叫阿黄"})
    ).status_code == 200

    refused = await client.post(f"/api/v1/memory/settle/{session_id}/resettle")
    assert refused.status_code == 409 and "人工改动" in refused.json()["detail"]


async def test_review_writes_answer_503_without_the_review_repo(api, store: SQLiteDatabase) -> None:
    """没装审核仓储：改动端点 503；档案列表照常返回，只是不带审核状态。"""
    client = await api(session_repo=store.sessions, archive_repo=store.archive)
    session_id = await _ended_session(store)
    line_id = await store.archive.insert_line(live_session_id=session_id, text="档案", quote="档案", evidence_chat_id=1)

    assert (await client.put(f"/api/v1/memory/archive/lines/{line_id}/title", json={"text": "新"})).status_code == 503
    body = (await client.get(f"/api/v1/memory/archive/{session_id}")).json()
    assert body["items"][0]["in_snapshot"] is False and body["snapshot_session_id"] is None
    assert (await client.delete(f"/api/v1/memory/archive/lines/{line_id}")).status_code == 200


async def test_candidates_list_masks_known_names_and_approve_or_reject(client, store: SQLiteDatabase) -> None:
    """候选审核区：弹幕原文里的已知昵称遮成某位观众；批准观众定下的写成档案行，驳回的不再待审。"""
    session_id = await _ended_session(store)
    await _settled(store, session_id, [])
    viewer = await store.viewers.resolve_viewer(
        platform="bilibili",
        id_kind="account",
        external_id="u_cand_1",
        user_name="阿杰",
        timestamp_ms=1_000,
        name_match_window_ms=0,
    )
    said = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_100,
        sender_role="viewer",
        content="阿杰提议：小羊叫雪球",
        message_type="danmaku",
        viewer_id=viewer.viewer_id,
    )
    reply = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_200,
        sender_role="assistant",
        content="好，就叫雪球",
        message_type="text",
    )
    other = await store.chat.insert_live_chat(
        live_session_id=session_id,
        timestamp_ms=1_300,
        sender_role="assistant",
        content="猫也起个名",
        message_type="text",
    )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=session_id,
            candidates=[
                PendingViewerCandidate(
                    kind="viewer_defined",
                    title="小羊取名雪球",
                    viewer_chat_id=said,
                    viewer_quote="阿杰提议：小羊叫雪球",
                    reply_chat_id=reply,
                    reply_quote="就叫雪球",
                ),
                PendingViewerCandidate(
                    kind="viewer_defined", title="猫的名字", reply_chat_id=other, reply_quote="猫也起个名"
                ),
            ],
            created_at_ms=2_000,
        )

    body = (await client.get("/api/v1/memory/candidates")).json()
    assert body["pending_count"] == 2
    named = next(item for item in body["items"] if item["title"] == "小羊取名雪球")
    assert named["viewer_quote"] == "某位观众提议：小羊叫雪球"
    assert (await client.get("/api/v1/memory/candidates", params={"status": "也许"})).status_code == 422

    approved = await client.post(
        f"/api/v1/memory/candidates/{named['id']}/approve", json={"title": "和观众一起给小羊取名雪球", "lasting": True}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    lines = (await client.get(f"/api/v1/memory/archive/{session_id}")).json()["items"]
    assert [(line["text"], line["pinned"], line["lasting"]) for line in lines] == [
        ("和观众一起给小羊取名雪球", True, True)
    ]

    rest = next(item for item in body["items"] if item["title"] == "猫的名字")
    assert (await client.post(f"/api/v1/memory/candidates/{rest['id']}/reject")).status_code == 200
    assert (await client.post(f"/api/v1/memory/candidates/{rest['id']}/reject")).status_code == 409
    after = (await client.get("/api/v1/memory/candidates", params={"status": "all"})).json()
    assert after["pending_count"] == 0
    assert sorted(item["status"] for item in after["items"]) == ["approved", "rejected"]


async def test_claimed_missing_candidates_come_with_the_nearest_ledger_entries(client, store: SQLiteDatabase) -> None:
    """她说欠着的候选旁附账上最接近的几条（未了结、已了结；判误记的不算），一条不沾就是空列表。"""
    session_id = await _ended_session(store)
    await _settled(store, session_id, [])
    sing = await store.commitments.insert_commitment(what="给大家唱小幸运", quote="下次唱小幸运", now_ms_value=10)
    draw = await store.commitments.insert_commitment(what="答应过要画的画", now_ms_value=11)
    voided = await store.commitments.insert_commitment(what="唱小幸运给你听", now_ms_value=12)
    await store.commitments.insert_commitment(what="修村口的桥", now_ms_value=13)
    assert sing and draw and voided
    assert await store.commitments.close_commitment(commitment_id=draw)
    assert await store.commitments.void_commitment(commitment_id=voided)
    lines = []
    for index, content in enumerate(("上次答应过你们唱小幸运", "还欠大家一个开箱视频")):
        lines.append(
            await store.chat.insert_live_chat(
                live_session_id=session_id,
                timestamp_ms=1_100 + index,
                sender_role="assistant",
                content=content,
                message_type="text",
            )
        )
    with store.manager.transaction() as conn:
        replace_session_candidates(
            conn,
            live_session_id=session_id,
            candidates=[
                PendingViewerCandidate(
                    kind="claimed_missing", title="唱小幸运", reply_chat_id=lines[0], reply_quote="上次答应过你们唱小幸运"
                ),
                PendingViewerCandidate(
                    kind="claimed_missing", title="开箱视频", reply_chat_id=lines[1], reply_quote="还欠大家一个开箱视频"
                ),
            ],
            created_at_ms=2_000,
        )

    items = {item["title"]: item for item in (await client.get("/api/v1/memory/candidates")).json()["items"]}
    assert [(near["id"], near["status"]) for near in items["唱小幸运"]["nearest_commitments"]] == [
        (sing, "open"),
        (draw, "closed"),
    ]
    assert items["开箱视频"]["nearest_commitments"] == []
