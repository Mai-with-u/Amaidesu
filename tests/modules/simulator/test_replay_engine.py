"""ReplayEngine 测试：录制读回（live_chat 业务表）、过滤、节奏调度、队列耗尽语义。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, List

import pytest

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
    extra_speech_rows: bool = False,
    empty_content_rows: bool = False,
) -> None:
    """向 live_chat 表写入一天的弹幕（可选混入主播发言行/空 content 行）。"""
    base = _day_base_ms(date_str)
    for p in payloads:
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=p.timestamp_ms,
            sender_role="viewer",
            sender_id=p.user.id,
            sender_name=p.user.name,
            content=p.content,
            message_type="danmaku",
            simulated=p.simulated,
        )
    if extra_speech_rows:
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base + 500_000,
            sender_role="assistant",
            sender_name="主播",
            content="主播发言行（不入回放）",
            message_type="speech",
        )
    if empty_content_rows:
        await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=base + 600_000,
            sender_role="viewer",
            sender_id="uid_x",
            sender_name="观众X",
            content="",
            message_type="danmaku",
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
    return ReplayEngine(SimulatorConfigSchema(**cfg_kwargs), chat_repo=store.chat)


DATE = "2026-09-01"


@pytest.mark.asyncio
async def test_load_filters_non_danmaku(store: SQLiteDatabase) -> None:
    """非弹幕行（主播发言等）不入回放队列。"""
    await _seed_day(
        store,
        DATE,
        [_payload(content="弹幕", ts_ms=_day_base_ms(DATE) + 1000)],
        extra_speech_rows=True,
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
    assert first.simulated is True
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
async def test_empty_content_row_skipped(store: SQLiteDatabase) -> None:
    """空 content 的弹幕行跳过不中断读取。"""
    await _seed_day(
        store,
        DATE,
        [_payload(content="好数据", ts_ms=_day_base_ms(DATE) + 1000)],
        empty_content_rows=True,
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1


@pytest.mark.asyncio
async def test_row_fields_restored_to_payload(store: SQLiteDatabase) -> None:
    """业务行字段正确还原为 payload：user/content/message_id/simulated。"""
    base = _day_base_ms(DATE)
    await store.chat.insert_live_chat(
        live_session_id=7,
        timestamp_ms=base + 1000,
        sender_role="viewer",
        sender_id="uid_观众甲",
        sender_name="观众甲",
        content="还原检查",
        message_type="danmaku",
        message_id="msg-001",
        simulated=True,
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1
    loaded = engine.pop_next()
    assert loaded is not None
    assert loaded.user == RoomMessageUser(id="uid_观众甲", name="观众甲")
    assert loaded.content == "还原检查"
    assert loaded.message_id == "msg-001"
    assert loaded.simulated is True


@pytest.mark.asyncio
async def test_replay_forces_simulated_true(store: SQLiteDatabase) -> None:
    """录制行 simulated=0（真实弹幕）回放时也一律标记 simulated=True。"""
    base = _day_base_ms(DATE)
    await store.chat.insert_live_chat(
        live_session_id=7,
        timestamp_ms=base + 1000,
        sender_role="viewer",
        sender_id="uid_真实观众",
        sender_name="真实观众",
        content="真实历史弹幕",
        message_type="danmaku",
        simulated=False,
    )

    engine = _engine(store)
    assert await engine.load(DATE, simulated_only=False) == 1
    loaded = engine.pop_next()
    assert loaded is not None
    assert loaded.simulated is True


@pytest.mark.asyncio
async def test_live_session_id_normalized_to_zero(store: SQLiteDatabase) -> None:
    """历史场次主键不入回放队列：live_session_id 统一清零，由场次盖章归属当前场次。"""
    base = _day_base_ms(DATE)
    await store.chat.insert_live_chat(
        live_session_id=42,  # 历史场次主键
        timestamp_ms=base + 1000,
        sender_role="viewer",
        sender_id="uid_观众甲",
        sender_name="观众甲",
        content="旧场次弹幕",
        message_type="danmaku",
    )

    engine = _engine(store)
    assert await engine.load(DATE) == 1
    loaded = engine.pop_next()
    assert loaded is not None and loaded.live_session_id == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
