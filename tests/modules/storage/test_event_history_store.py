"""event_history 表存储 + EventHistoryService 持久化/回灌 单测"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest

from src.modules.events.event_history import EventHistoryService, EventRecord
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
async def store(tmp_path: Path):
    """直接使用 SQLiteDatabase：断言用裸 SQL，写入用 db.events 仓储。"""
    db = SQLiteDatabase(tmp_path / "event-history.db")
    await db.initialize()
    yield db
    await db.close()


def _day_base_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


async def _wait_for_row_count(store: SQLiteDatabase, expected: int, timeout: float = 3.0) -> None:
    """等待 fire-and-forget 写库任务落定（轮询直到行数达标）。"""

    async def _poll() -> None:
        while True:
            rows = await store.execute("SELECT COUNT(*) AS n FROM event_history")
            if int(rows[0]["n"]) >= expected:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


@pytest.mark.asyncio
async def test_insert_and_roundtrip_by_date(store: SQLiteDatabase) -> None:
    """插入事件行后按本地日期读回，payload 原样保留。"""
    payload = {"message_type": "danmaku", "content": "你好", "timestamp_ms": 123}
    await store.events.insert_event(
        record_id="rec-1",
        event_name="room.message.danmaku",
        timestamp_ms=_day_base_ms("2026-09-01") + 1000,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )

    dates = await store.events.list_event_dates("room.message.danmaku")
    assert dates == ["2026-09-01"]

    rows = await store.events.get_day_events("2026-09-01", event_name="room.message.danmaku")
    assert len(rows) == 1
    assert rows[0]["record_id"] == "rec-1"
    assert json.loads(rows[0]["payload"]) == payload


@pytest.mark.asyncio
async def test_list_event_dates_filters_by_name_and_sorted(store: SQLiteDatabase) -> None:
    """日期清单按事件名过滤、时间正序。"""
    for day, name in [("2026-09-02", "room.message.danmaku"), ("2026-09-01", "room.message.danmaku"), ("2026-09-01", "core.error")]:
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=name,
            timestamp_ms=_day_base_ms(day) + 1000,
            payload_json="{}",
        )

    assert await store.events.list_event_dates("room.message.danmaku") == ["2026-09-01", "2026-09-02"]
    assert await store.events.list_event_dates("core.error") == ["2026-09-01"]
    assert await store.events.list_event_dates("game.milestone") == []


@pytest.mark.asyncio
async def test_get_day_events_without_name_returns_all(store: SQLiteDatabase) -> None:
    """event_name 缺省时取当日全部事件。"""
    base = _day_base_ms("2026-09-01")
    for idx, name in enumerate(["room.message.danmaku", "core.startup"]):
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=name,
            timestamp_ms=base + idx * 1000,
            payload_json="{}",
        )

    rows = await store.events.get_day_events("2026-09-01")
    assert {row["event_name"] for row in rows} == {"room.message.danmaku", "core.startup"}


@pytest.mark.asyncio
async def test_service_record_persists_with_event_name_fallback(store: SQLiteDatabase) -> None:
    """service.record 异步写库；event_name 缺省时退回 type。"""
    service = EventHistoryService(max_events=10, persist=True, event_repo=store.events)
    service.record(
        EventRecord(
            id="evt-1",
            type="room.message",
            event_name="room.message.danmaku",
            timestamp_ms=_day_base_ms("2026-09-01") + 1000,
            source="test",
            summary="弹幕",
            data={"content": "你好"},
        )
    )
    service.record(
        EventRecord(
            id="evt-2",
            type="system.status",
            source="test",
            summary="状态",
            data={"event": "core.startup"},
        )
    )
    await _wait_for_row_count(store, 2)

    rows = await store.execute("SELECT record_id, event_name FROM event_history ORDER BY record_id")
    names = {row["record_id"]: row["event_name"] for row in rows}
    assert names["evt-1"] == "room.message.danmaku"
    assert names["evt-2"] == "system.status"  # event_name 空 → 退回 type


@pytest.mark.asyncio
async def test_service_record_without_store_keeps_buffer_only() -> None:
    """persist=True 但未注入 store：仅内存缓冲，不抛错。"""
    service = EventHistoryService(max_events=10, persist=True, event_repo=None)
    service.record(EventRecord(id="evt-1", type="room.message", source="test", summary="s"))
    assert service.get_recent(1)[0].id == "evt-1"


@pytest.mark.asyncio
async def test_backfill_today_from_store(tmp_path: Path) -> None:
    """启动回灌：当日的库记录进入环形缓冲（跨重启不丢当日历史）。"""
    db_path = tmp_path / "backfill.db"
    writer = SQLiteDatabase(db_path)
    await writer.initialize()
    today = datetime.now().strftime("%Y-%m-%d")
    base = _day_base_ms(today)
    for idx in range(3):
        await writer.events.insert_event(
            record_id=f"rec-{idx}",
            event_name="room.message.danmaku",
            timestamp_ms=base + idx * 1000,
            payload_json='{"content": "回灌"}',
        )
    await writer.close()

    reader = SQLiteDatabase(db_path)
    await reader.initialize()
    try:
        service = EventHistoryService(max_events=100, persist=True, event_repo=reader.events)
        count = await service.backfill_today_from_store()
        assert count == 3
        recent = service.get_recent(limit=10)
        assert [r.id for r in reversed(recent)] == ["rec-0", "rec-1", "rec-2"]
        assert recent[-1].data == {"content": "回灌"}
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_backfill_skips_non_today_rows(store: SQLiteDatabase) -> None:
    """回灌只取当日：非当日记录不进缓冲。"""
    await store.events.insert_event(
        record_id="old-row",
        event_name="room.message.danmaku",
        timestamp_ms=_day_base_ms("2026-01-01"),
        payload_json="{}",
    )
    service = EventHistoryService(max_events=100, persist=True, event_repo=store.events)
    count = await service.backfill_today_from_store()
    assert count == 0


@pytest.mark.asyncio
async def test_uuid_record_id_roundtrip(store: SQLiteDatabase) -> None:
    """uuid 形态 record_id 全链路无损。"""
    rid = str(uuid.uuid4())
    await store.events.insert_event(
        record_id=rid,
        event_name="core.startup",
        timestamp_ms=_day_base_ms("2026-09-01"),
        payload_json="{}",
    )
    rows = await store.events.get_day_events("2026-09-01", event_name="core.startup")
    assert rows[0]["record_id"] == rid
