"""ReplayEngine 测试：录制读回、过滤、节奏调度、队列耗尽语义。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.events.event_history import EventRecord
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.simulator.config_schema import SimulatorConfigSchema
from src.modules.simulator.replay_engine import ReplayEngine


@pytest.fixture
def record_dir(tmp_path: Path) -> Path:
    return tmp_path / "events"


def _write_day(record_dir: Path, date_str: str, payloads: list[RoomMessagePayload]) -> None:
    """按 EventHistoryService 的落盘格式写一天录制（type + data=payload dump）"""
    record_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        EventRecord(type="room.message.danmaku", source="test", data=p.model_dump(mode="json")).model_dump_json()
        for p in payloads
    ]
    (record_dir / f"{date_str}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _payload(
    *,
    content: str,
    ts_ms: int,
    user: str = "观众甲",
    simulated: bool = False,
) -> RoomMessagePayload:
    return RoomMessagePayload(
        live_session_id="live",
        message_type="danmaku",
        user=RoomMessageUser(id=f"uid_{user}", name=user),
        content=content,
        timestamp_ms=ts_ms,
        simulated=simulated,
    )


def _engine(record_dir: Path, **cfg_kwargs) -> ReplayEngine:
    return ReplayEngine(SimulatorConfigSchema(**cfg_kwargs), persist_dir=record_dir)


def test_load_filters_non_room_message(record_dir: Path) -> None:
    """非 room.message.danmaku 事件不入回放队列。"""
    date = "2026-09-01"
    _write_day(record_dir, date, [_payload(content="弹幕", ts_ms=1000)])
    # 混入无关事件
    with (record_dir / f"{date}.jsonl").open("a", encoding="utf-8") as f:
        f.write(EventRecord(type="planner.checkpoint", source="test", data={}).model_dump_json() + "\n")

    engine = _engine(record_dir)
    assert engine.load(date) == 1


def test_load_simulated_only_filter(record_dir: Path) -> None:
    """simulated_only=True 时跳过录制中的真实消息。"""
    _write_day(
        record_dir,
        "2026-09-01",
        [
            _payload(content="真实弹幕", ts_ms=1000, simulated=False),
            _payload(content="模拟弹幕", ts_ms=2000, simulated=True),
        ],
    )

    engine = _engine(record_dir)
    assert engine.load("2026-09-01", simulated_only=True) == 1
    first = engine.pop_next()
    assert first is not None and first.content == "模拟弹幕"
    assert engine.pop_next() is None


def test_gap_seconds_by_timestamp_diff_and_speed(record_dir: Path) -> None:
    """间隔 = 相邻 timestamp_ms 差 / 速度倍率。"""
    _write_day(
        record_dir,
        "2026-09-01",
        [
            _payload(content="第一条", ts_ms=0),
            _payload(content="第二条", ts_ms=10_000),
        ],
    )
    engine = _engine(record_dir, replay_speed=2.0, replay_gap_cap_s=60.0)
    engine.load("2026-09-01")

    assert engine.next_gap_seconds() == 0.0  # 首条无前驱
    engine.pop_next()
    assert engine.next_gap_seconds() == pytest.approx(5.0)  # 10s / 2x


def test_gap_cap_truncates_long_silence(record_dir: Path) -> None:
    """超长冷场按 replay_gap_cap_s 截断。"""
    _write_day(
        record_dir,
        "2026-09-01",
        [
            _payload(content="第一条", ts_ms=0),
            _payload(content="第二条", ts_ms=600_000),  # 10 分钟后
        ],
    )
    engine = _engine(record_dir, replay_speed=1.0, replay_gap_cap_s=60.0)
    engine.load("2026-09-01")
    engine.pop_next()

    assert engine.next_gap_seconds() == 60.0


def test_pop_exhaustion_returns_none(record_dir: Path) -> None:
    """队列耗尽后 pop_next 返回 None，remaining 归零。"""
    _write_day(record_dir, "2026-09-01", [_payload(content="唯一一条", ts_ms=0)])

    engine = _engine(record_dir)
    engine.load("2026-09-01")
    assert engine.total == 1
    assert engine.pop_next() is not None
    assert engine.pop_next() is None
    assert engine.remaining == 0


def test_load_missing_date_returns_zero(record_dir: Path) -> None:
    """无录制文件的日期加载 0 条，replay_date 置 None。"""
    engine = _engine(record_dir)
    assert engine.load("2099-01-01") == 0
    assert engine.replay_date is None


def test_corrupt_line_skipped(record_dir: Path) -> None:
    """坏行跳过不中断读取。"""
    date = "2026-09-01"
    _write_day(record_dir, date, [_payload(content="好数据", ts_ms=1000)])
    with (record_dir / f"{date}.jsonl").open("a", encoding="utf-8") as f:
        f.write("{ this is not json }\n")
        f.write(json.dumps({"id": "x", "type": "room.message.danmaku"}) + "\n")  # 缺必填字段

    engine = _engine(record_dir)
    assert engine.load(date) == 1
