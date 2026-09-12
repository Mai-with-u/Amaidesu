"""ReplayEngine 测试：录制读回（event_history 表）、过滤、节奏调度、队列耗尽语义。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, List, Optional

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.replay_engine import ReplayEngine
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(tmp_path / "replay.db")
    await s.initialize()
    yield s
    await s.close()


def _day_base_ms(date_str: str) -> int:
    """本地日期零点的 epoch 毫秒（与 store 按本地日过滤的口径一致）。"""
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)


async def _seed_day(
    store: SQLiteDatabase,
    date_str: str,
    payloads: List[RoomMessagePayload],
    *,
    extra_event_names: Optional[List[str]] = None,
    corrupt_payloads: bool = False,
) -> None:
    """向 event_history 表写入一天的录制（danmaku + 可选混入其他事件/坏 payload）。"""
    base = _day_base_ms(date_str)
    for p in payloads:
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
            timestamp_ms=p.timestamp_ms,
            payload_json=p.model_dump_json(),
        )
    for offset, event_name in enumerate(extra_event_names or []):
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=event_name,
            timestamp_ms=base + offset * 1000,
            payload_json="{}",
        )
    if corrupt_payloads:
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
            timestamp_ms=base + 500_000,
            payload_json="{ this is not json }",
        )
        await store.events.insert_event(
            record_id=str(uuid.uuid4()),
            event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
            timestamp_ms=base + 600_000,
            payload_json='{"id": "x"}',  # 缺必填字段
        )


def _payload(
    *,
    content: str,
    ts_ms: int,
    user: str = "观众甲",
    simulated: bool = False,
) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id=f"uid_{user}", name=user),
        content=content,
        timestamp_ms=ts_ms,
        simulated=simulated,
    )


def _engine(store: SQLiteDatabase, **cfg_kwargs) -> ReplayEngine:
    return ReplayEngine(SimulatorConfigSchema(**cfg_kwargs), event_repo=store.events)


DATE = "2026-09-01"


@pytest.mark.asyncio
async def test_load_filters_non_danmaku(store: SQLiteDatabase) -> None:
    """非 room.message.danmaku 事件不入回放队列。"""
    await _seed_day(
        store,
        DATE,
        [_payload(content="弹幕", ts_ms=_day_base_ms(DATE) + 1000)],
        extra_event_names=["planner.checkpoint"],
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1


@pytest.mark.asyncio
async def test_load_simulated_only_filter(store: SQLiteDatabase) -> None:
    """simulated_only=True 时跳过录制中的真实消息。"""
    base = _day_base_ms(DATE)
    await _seed_day(
        store,
        DATE,
        [
            _payload(content="真实弹幕", ts_ms=base + 1000, simulated=False),
            _payload(content="模拟弹幕", ts_ms=base + 2000, simulated=True),
        ],
    )

    engine = _engine(store)
    assert await engine.load(DATE, simulated_only=True) == 1
    first = engine.pop_next()
    assert first is not None and first.content == "模拟弹幕"
    assert engine.pop_next() is None


@pytest.mark.asyncio
async def test_gap_seconds_by_timestamp_diff_and_speed(store: SQLiteDatabase) -> None:
    """间隔 = 相邻 timestamp_ms 差 / 速度倍率。"""
    base = _day_base_ms(DATE)
    await _seed_day(
        store,
        DATE,
        [
            _payload(content="第一条", ts_ms=base),
            _payload(content="第二条", ts_ms=base + 10_000),
        ],
    )
    engine = _engine(store, replay_speed=2.0, replay_gap_cap_s=60.0)
    await engine.load(DATE)

    assert engine.next_gap_seconds() == 0.0  # 首条无前驱
    engine.pop_next()
    assert engine.next_gap_seconds() == pytest.approx(5.0)  # 10s / 2x


@pytest.mark.asyncio
async def test_gap_cap_truncates_long_silence(store: SQLiteDatabase) -> None:
    """超长冷场按 replay_gap_cap_s 截断。"""
    base = _day_base_ms(DATE)
    await _seed_day(
        store,
        DATE,
        [
            _payload(content="第一条", ts_ms=base),
            _payload(content="第二条", ts_ms=base + 600_000),  # 10 分钟后
        ],
    )
    engine = _engine(store, replay_speed=1.0, replay_gap_cap_s=60.0)
    await engine.load(DATE)
    engine.pop_next()

    assert engine.next_gap_seconds() == 60.0


@pytest.mark.asyncio
async def test_pop_exhaustion_returns_none(store: SQLiteDatabase) -> None:
    """队列耗尽后 pop_next 返回 None，remaining 归零。"""
    await _seed_day(store, DATE, [_payload(content="唯一一条", ts_ms=_day_base_ms(DATE))])

    engine = _engine(store)
    await engine.load(DATE)
    assert engine.total == 1
    assert engine.pop_next() is not None
    assert engine.pop_next() is None
    assert engine.remaining == 0


@pytest.mark.asyncio
async def test_load_missing_date_returns_zero(store: SQLiteDatabase) -> None:
    """无录制记录的日期加载 0 条，replay_date 置 None。"""
    engine = _engine(store)
    assert await engine.load("2099-01-01") == 0
    assert engine.replay_date is None


@pytest.mark.asyncio
async def test_corrupt_payload_skipped(store: SQLiteDatabase) -> None:
    """坏 payload 跳过不中断读取。"""
    await _seed_day(
        store,
        DATE,
        [_payload(content="好数据", ts_ms=_day_base_ms(DATE) + 1000)],
        corrupt_payloads=True,
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1


@pytest.mark.asyncio
async def test_live_session_id_non_int_normalized(store: SQLiteDatabase) -> None:
    """旧录制 live_session_id 非整数（房间字符串时代）统一清零。"""
    base = _day_base_ms(DATE)
    p = _payload(content="旧场次弹幕", ts_ms=base + 1000)
    data = p.model_dump(mode="json")
    data["live_session_id"] = "room_123"  # 模拟旧录制（原始 payload，未经过模型校验）
    await store.events.insert_event(
        record_id=str(uuid.uuid4()),
        event_name=CoreEvents.ROOM_MESSAGE_DANMAKU,
        timestamp_ms=p.timestamp_ms,
        payload_json=json.dumps(data, ensure_ascii=False),
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1
    loaded = engine.pop_next()
    assert loaded is not None and loaded.live_session_id == 0
