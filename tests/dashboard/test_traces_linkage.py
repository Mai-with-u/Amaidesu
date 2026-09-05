"""Trace 聚合 API 单元测试。

覆盖链路键提取、三段聚合与 REST 端点：
- 链路键 = room.message 扁平 payload 顶层 ``id``（BasePayload uuid，
  与 EventRecord.id / WS 消息 id 同源）
- messages 段按链路键精确对齐；planning/execution 段暂为空数组（无关联键）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.modules.dashboard.api.traces import (
    _build_trace,
    _extract_message_id,
    _find_room_message_event,
    get_trace,
    list_traces,
)
from src.modules.events.event_history import EventHistoryService, EventRecord


def _room_message_record(message_id: str, content: str = "主播好可爱") -> EventRecord:
    return EventRecord(
        id=message_id,
        type="room.message",
        level="info",
        source="ls_test",
        summary=f"[danmaku] {content}",
        data={
            "id": message_id,
            "live_session_id": "ls_test",
            "message_type": "danmaku",
            "user": {"id": "u1", "name": "观众A"},
            "content": content,
            "gift": None,
            "sc": None,
            "timestamp_ms": 1_706_745_600_000,
            "simulated": False,
        },
    )


def test_extract_message_id_from_flat_payload() -> None:
    record = _room_message_record("msg-1")
    assert _extract_message_id(record.data) == "msg-1"


def test_extract_message_id_flat_only() -> None:
    """链路键只认扁平 payload 顶层 id；非 room.message 载荷（无 id）返回空。"""
    assert _extract_message_id({"id": "msg-1"}) == "msg-1"
    assert _extract_message_id({}) == ""
    assert _extract_message_id({"message": {"message_id": "nested"}}) == ""


def test_find_and_build_trace() -> None:
    history = EventHistoryService(max_events=100, persist=False)
    history.record(_room_message_record("msg-1"))
    history.record(
        EventRecord(
            id="evt-planner",
            type="planner.checkpoint",
            level="info",
            source="streamer",
            summary="checkpoint",
            data={"id": "evt-planner"},
        )
    )

    found = _find_room_message_event(history, "msg-1")
    assert found is not None
    assert found.data["content"] == "主播好可爱"
    assert _find_room_message_event(history, "missing") is None

    trace = _build_trace(history, "msg-1")
    assert trace is not None
    assert trace["message_id"] == "msg-1"
    assert trace["message"]["text"] == "主播好可爱"
    assert trace["message"]["data_type"] == "danmaku"
    assert trace["message"]["user_nickname"] == "观众A"
    segments = trace["segments"]
    assert len(segments["messages"]) == 1
    assert segments["messages"][0]["timestamp_ms"] > 0
    # 决策/执行段与触发消息之间暂无关联键，诚实返回空数组
    assert segments["planning"] == []
    assert segments["execution"] == []


def test_build_trace_returns_none_for_unknown_id() -> None:
    history = EventHistoryService(max_events=100, persist=False)
    assert _build_trace(history, "no-such-id") is None


# ------------------------------------------------------------------ #
# REST 端点（绕过依赖注入，直接传 fake server）
# ------------------------------------------------------------------ #


def _fake_server(history: EventHistoryService) -> SimpleNamespace:
    return SimpleNamespace(event_history=history)


@pytest.mark.asyncio
async def test_list_traces_endpoint_returns_linkage() -> None:
    history = EventHistoryService(max_events=100, persist=False)
    history.record(_room_message_record("msg-1"))
    history.record(_room_message_record("msg-2", content="第二条"))

    result = await list_traces(limit=20, server=_fake_server(history))
    assert result["total"] == 2
    ids = {t["message_id"] for t in result["traces"]}
    assert ids == {"msg-1", "msg-2"}
    for trace in result["traces"]:
        assert len(trace["segments"]["messages"]) == 1
        assert trace["segments"]["planning"] == []
        assert trace["segments"]["execution"] == []


@pytest.mark.asyncio
async def test_get_trace_endpoint() -> None:
    history = EventHistoryService(max_events=100, persist=False)
    history.record(_room_message_record("msg-1"))

    found = await get_trace("msg-1", server=_fake_server(history))
    assert found["trace"] is not None
    assert found["trace"]["message"]["text"] == "主播好可爱"

    missing = await get_trace("no-such", server=_fake_server(history))
    assert missing["trace"] is None
    assert "no-such" in missing["error"]
