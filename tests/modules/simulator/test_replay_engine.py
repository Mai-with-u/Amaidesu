"""ReplayEngine：录制读回（live_chat 业务表）、过滤、节奏调度、队列耗尽语义"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.modules.events.payloads.room import RoomMessageUser
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.replay_engine import ReplayEngine
from src.modules.storage.database import SQLiteDatabase

DATE = "2026-09-01"
BASE_MS = int(datetime.strptime(DATE, "%Y-%m-%d").timestamp() * 1000)  # 本地日零点，与 store 按本地日过滤的口径一致


async def _insert(store: SQLiteDatabase, content: str, offset_ms: int, **overrides: Any) -> None:
    row = {
        "live_session_id": 1,
        "timestamp_ms": BASE_MS + offset_ms,
        "sender_role": "viewer",
        "sender_id": "uid_观众甲",
        "sender_name": "观众甲",
        "content": content,
        "message_type": "danmaku",
        **overrides,
    }
    await store.chat.insert_live_chat(**row)


def _engine(store: SQLiteDatabase, **cfg: Any) -> ReplayEngine:
    return ReplayEngine(SimulatorConfigSchema(**cfg), chat_repo=store.chat)


async def test_load_keeps_only_non_empty_danmaku_rows_and_exhausts_cleanly(store: SQLiteDatabase) -> None:
    await _insert(store, "唯一一条", 1000)
    await _insert(store, "主播发言行（不入回放）", 500_000, sender_role="assistant", message_type="speech")
    await _insert(store, "", 600_000, sender_id="uid_x")  # 空 content 行跳过但不中断读取

    engine = _engine(store)
    assert await engine.load(DATE) == 1 and engine.total == 1
    assert engine.pop_next() is not None
    assert engine.pop_next() is None and engine.remaining == 0  # 队列耗尽后返回 None

    assert await engine.load("2099-01-01") == 0 and engine.replay_date is None  # 无录制的日期


async def test_simulated_only_filter_skips_real_messages(store: SQLiteDatabase) -> None:
    await _insert(store, "真实弹幕", 1000, simulated=False)
    await _insert(store, "模拟弹幕", 2000, simulated=True)

    engine = _engine(store)
    assert await engine.load(DATE, simulated_only=True) == 1
    first = engine.pop_next()
    assert first is not None and first.content == "模拟弹幕" and first.simulated is True
    assert engine.pop_next() is None


async def test_gap_follows_timestamp_diff_speed_and_cap(store: SQLiteDatabase) -> None:
    await _insert(store, "第一条", 0)
    await _insert(store, "第二条", 10_000)
    await _insert(store, "第三条", 10_000 + 600_000)  # 十分钟冷场

    engine = _engine(store, replay_speed=2.0, replay_gap_cap_s=60.0)
    await engine.load(DATE)
    assert engine.next_gap_seconds() == 0.0  # 首条无前驱
    engine.pop_next()
    assert engine.next_gap_seconds() == pytest.approx(5.0)  # 10 秒 / 2 倍速
    engine.pop_next()
    assert engine.next_gap_seconds() == 60.0  # 超长冷场按上限截断


async def test_rows_are_restored_as_simulated_payloads_without_historical_session(store: SQLiteDatabase) -> None:
    await _insert(
        store, "还原检查", 1000, live_session_id=42, sender_id="uid_观众甲", message_id="msg-001", simulated=False
    )

    engine = _engine(store)
    assert await engine.load(DATE, simulated_only=False) == 1
    loaded = engine.pop_next()
    assert loaded is not None
    assert loaded.user == RoomMessageUser(id="uid_观众甲", name="观众甲")
    assert (loaded.content, loaded.message_id) == ("还原检查", "msg-001")
    assert loaded.simulated is True  # 真实历史弹幕回放时也一律标记为模拟
    assert loaded.live_session_id == 0  # 历史场次主键清零，由场次盖章归属当前场次
