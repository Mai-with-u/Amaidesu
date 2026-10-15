"""Sessions API：场次列表 / 开启 / 结束 / 重命名 / 删除，以及回看时间线（明细与事件历史按时间合并、发言去重、游标翻页）"""

from __future__ import annotations

import pytest

from src.modules.events.event_history import EventHistoryService, EventRecord

BASE = 1_700_000_000_000


@pytest.fixture
def event_history() -> EventHistoryService:
    return EventHistoryService(max_events=100)


@pytest.fixture
async def sessions_client(api, event_bus, session_manager, event_history):
    return await api(event_bus=event_bus, session_manager=session_manager, event_history=event_history)


async def _open(client, title: str = "") -> int:
    return (await client.post("/api/v1/live-sessions/open", json={"title": title} if title else {})).json()[
        "live_session_id"
    ]


def _decision(pk: int, seq: int, ts: int, **data) -> EventRecord:
    return EventRecord(
        id=f"evt-{seq}",
        type="planner.decision",
        level="info",
        source=str(pk),
        summary=f"决策{seq}",
        data={"live_session_id": pk, "round_id": f"rnd_{seq}", **data},
        timestamp_ms=ts,
    )


async def test_open_list_close_flow(sessions_client) -> None:
    client = sessions_client
    # 启动不自动开新场次
    body = (await client.get("/api/v1/live-sessions")).json()
    assert (body["active_session_id"], body["items"]) == (None, [])

    pk = await _open(client, "晚间场")
    body = (await client.get("/api/v1/live-sessions")).json()
    assert body["active_session_id"] == pk
    (active,) = [item for item in body["items"] if item["is_active"]]
    assert (active["title"], active["ended_at_ms"]) == ("晚间场", None)

    # 空场次结束 → 整行丢弃（防膨胀）
    assert (await client.post(f"/api/v1/live-sessions/{pk}/close")).json()["success"] is True
    body = (await client.get("/api/v1/live-sessions")).json()
    assert body["active_session_id"] is None and all(i["live_session_id"] != pk for i in body["items"])


async def test_close_mismatched_and_delete_missing(sessions_client) -> None:
    client = sessions_client
    await _open(client)
    await _open(client)  # 开第二场时第一场已被自动结束
    active = (await client.get("/api/v1/live-sessions")).json()["active_session_id"]
    assert (await client.post(f"/api/v1/live-sessions/{active + 100000}/close")).status_code == 409

    pk = await _open(client, "待删")
    deleted = await client.delete(f"/api/v1/live-sessions/{pk}")
    assert (deleted.status_code, deleted.json()["success"]) == (200, True)
    assert (await client.delete(f"/api/v1/live-sessions/{pk}")).status_code == 404


async def test_rename_session(sessions_client) -> None:
    """重命名：进行中场次可直接改名；空串规范化为清除标题；不存在的场次 404。"""
    client = sessions_client
    pk = await _open(client, "旧标题")

    renamed = await client.patch(f"/api/v1/live-sessions/{pk}", json={"title": "  新标题  "})
    assert (renamed.status_code, renamed.json()["success"]) == (200, True)

    async def titles() -> dict:
        return {i["live_session_id"]: i["title"] for i in (await client.get("/api/v1/live-sessions")).json()["items"]}

    assert (await titles())[pk] == "新标题"
    cleared = await client.patch(f"/api/v1/live-sessions/{pk}", json={"title": "   "})
    assert (cleared.status_code, cleared.json()["detail"]) == (200, "场次标题已清除")
    assert (await titles())[pk] is None
    assert (await client.patch(f"/api/v1/live-sessions/{pk + 99999}", json={"title": "x"})).status_code == 404


async def test_list_filters_by_source_and_title(sessions_client, session_manager) -> None:
    """前两个显式场次都需有明细，否则自动结束时会被整行丢弃。"""
    client = sessions_client
    first = await _open(client, "筛选测试场")
    await session_manager.chat.insert_live_chat(
        live_session_id=first, timestamp_ms=BASE, sender_role="viewer", content="让这场不被丢弃", message_type="danmaku"
    )
    second = await _open(client, "另一个场次")
    await client.post(f"/api/v1/live-sessions/{second}/close")

    manual = (await client.get("/api/v1/live-sessions", params={"source": "manual"})).json()
    assert [i["title"] for i in manual["items"]] == ["筛选测试场"]
    keyword = (await client.get("/api/v1/live-sessions", params={"q": "筛选测试"})).json()
    assert [i["title"] for i in keyword["items"]] == ["筛选测试场"]
    assert (await client.get("/api/v1/live-sessions", params={"source": "hacker"})).status_code == 400


async def test_timeline_merges_details_and_events_and_dedupes_speech(
    sessions_client, session_manager, event_history
) -> None:
    client = sessions_client
    pk = await _open(client, "回看测试")
    chat = session_manager.chat
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE, sender_role="viewer", sender_id="u1", sender_name="观众A",
        content="主播玩什么？", message_type="danmaku", message_id="msg_seed_1",
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 5_000, sender_role="assistant", sender_name="主播",
        content="今天玩《双人成行》！", message_type="speak", reply_to_message_id="msg_seed_1",
    )  # fmt: skip
    # 礼物行带 total_price：响应模型 extra=forbid 下漏声明该字段曾使整响应 500
    await chat.insert_gift(
        live_session_id=pk, timestamp_ms=BASE + 6_000, user_id="u2", user_name="土豪B",
        gift_name="小花花", quantity=3, total_price=3000,
    )  # fmt: skip
    event_history.record(_decision(pk, 1, BASE + 4_000, reply_to_message_id="msg_seed_1"))
    # 发言双源去重：与 streamer.speech 事件同文本同时刻的明细行让位（事件侧是富卡）；无事件对应的明细行保留兜底
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 10_000, sender_role="assistant", sender_name="主播",
        content="有事件对应的发言", message_type="speak",
    )  # fmt: skip
    event_history.record(
        EventRecord(
            id="evt-speech-1", type="streamer.speech", level="info", source=str(pk), summary="发言",
            data={"live_session_id": pk, "text": "有事件对应的发言", "utterance_id": "utt_1"},
            timestamp_ms=BASE + 10_000,
        )
    )  # fmt: skip
    await chat.insert_live_chat(
        live_session_id=pk, timestamp_ms=BASE + 11_000, sender_role="assistant", sender_name="主播",
        content="只有明细行的发言", message_type="speak",
    )  # fmt: skip

    body = (await client.get(f"/api/v1/live-sessions/{pk}/timeline")).json()
    items = body["items"]
    gift = next(i for i in items if i["kind"] == "gift")
    assert (gift["gift_name"], gift["total_price"]) == ("小花花", 3000)
    decision = next(i for i in items if i["kind"] == "event" and i["event_type"] == "planner.decision")
    assert decision["data"]["round_id"] == "rnd_1"
    speech_rows = [i for i in items if i["kind"] == "speech"]
    assert {r["reply_to_message_id"] for r in speech_rows if r.get("reply_to_message_id")} == {"msg_seed_1"}
    assert "有事件对应的发言" not in [r["text"] for r in speech_rows]
    assert "只有明细行的发言" in [r["text"] for r in speech_rows]
    assert len([i for i in items if i["kind"] == "event" and i["event_type"] == "streamer.speech"]) == 1
    # 诊断未装配时不报裁剪
    assert (body["has_more"], body["events_pruned"]) == (False, False)


async def test_timeline_cursor_pagination(sessions_client, event_history) -> None:
    client = sessions_client
    pk = await _open(client, "游标翻页")
    for seq in range(1, 4):
        event_history.record(_decision(pk, seq, BASE + seq * 1_000))

    url = f"/api/v1/live-sessions/{pk}/timeline"
    page1 = (await client.get(url, params={"limit": 2})).json()
    assert [i["data"]["round_id"] for i in page1["items"]] == ["rnd_2", "rnd_3"] and page1["has_more"] is True
    page2 = (await client.get(url, params={"limit": 2, "before_timestamp_ms": page1["items"][0]["ts_ms"]})).json()
    assert [i["data"]["round_id"] for i in page2["items"]] == ["rnd_1"] and page2["has_more"] is False
