"""SessionEventReader 单测：环形缓冲 ∪ 诊断文件合并 / 去重 / 白名单 / 降级信号。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.modules.dashboard.services.session_events import (
    SessionEventReader,
    is_replay_event_type,
)
from src.modules.events.event_history import EventRecord, EventHistoryService

# 扫描范围按时间戳落在的本地日期枚举天目录，夹具统一用真实时刻
_DAY = datetime(2026, 10, 11, 21, 0, 0)
_START_MS = int(datetime(2026, 10, 11, 20, 0, 0).timestamp() * 1000)
_END_MS = int(datetime(2026, 10, 11, 23, 0, 0).timestamp() * 1000)


def _ms(minute: int) -> int:
    """场次日内第 minute 分钟对应的毫秒时刻（落在 2026-10-11 21 时文件）。"""
    return int((_DAY.replace(minute=minute)).timestamp() * 1000)


def _record(
    type_: str,
    ts_ms: int,
    session_id: int = 7,
    record_id: str = "",
    data_extra: dict | None = None,
) -> EventRecord:
    return EventRecord(
        id=record_id,
        type=type_,
        event_name=type_,
        timestamp_ms=ts_ms,
        level="info",
        source=str(session_id),
        summary="s",
        data={"live_session_id": session_id, **(data_extra or {})},
    )


def _write_hour_file(
    events_dir: Path,
    day: str,
    hour: int,
    records: list[EventRecord],
) -> None:
    day_dir = events_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    with open(day_dir / f"{hour}.jsonl", "w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def test_is_replay_event_type_covers_exact_and_prefix() -> None:
    assert is_replay_event_type("planner.decision")
    assert is_replay_event_type("game.milestone")
    assert is_replay_event_type("tool.result.maicraft_observe")
    assert not is_replay_event_type("room.message.danmaku")
    assert not is_replay_event_type("llm.context.used")
    assert not is_replay_event_type("task.changed")
    assert not is_replay_event_type("core.startup")


def test_collect_merges_buffer_and_files_with_dedupe(tmp_path: Path) -> None:
    """环形缓冲与文件两侧同 id 记录去重；结果按时间升序。"""
    history = EventHistoryService(max_events=100)
    buffered = _record("planner.decision", _ms(2), record_id="evt-1")
    history.record(buffered)

    events_dir = tmp_path / "events"
    _write_hour_file(
        events_dir,
        "2026-10-11",
        21,
        [
            buffered,  # 与环形缓冲同 id：去重
            _record("planner.verdict", _ms(1), record_id="evt-0"),
            _record("streamer.stage", _ms(3), record_id="evt-2"),
        ],
    )

    window = SessionEventReader(event_history=history, events_dir=events_dir).collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert [r.timestamp_ms for r in window.records] == [_ms(1), _ms(2), _ms(3)]
    assert window.days_pruned is False


def test_collect_filters_by_session_and_whitelist(tmp_path: Path) -> None:
    """他场事件与白名单外类型（含 task.changed）不进回看。"""
    events_dir = tmp_path / "events"
    _write_hour_file(
        events_dir,
        "2026-10-11",
        21,
        [
            _record("planner.decision", _ms(1), session_id=7),
            _record("planner.decision", _ms(1) + 10, session_id=8),  # 他场
            _record("task.changed", _ms(2), session_id=7),  # 非卡面
            _record("llm.context.used", _ms(3), session_id=7),  # 非卡面
            _record("tool.result.minecraft_glance", _ms(4), session_id=7),  # 动态族
        ],
    )
    window = SessionEventReader(events_dir=events_dir).collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert [r.type for r in window.records] == ["planner.decision", "tool.result.minecraft_glance"]


def test_collect_reports_pruned_when_day_dir_missing(tmp_path: Path) -> None:
    """时间范围内的天目录缺失 → days_pruned 置真（事件侧降级信号）。"""
    window = SessionEventReader(events_dir=tmp_path / "events").collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert window.records == []
    assert window.days_pruned is True


def test_collect_tolerates_malformed_lines(tmp_path: Path) -> None:
    """坏行跳过不抛错，好行照常收集。"""
    events_dir = tmp_path / "events"
    day_dir = events_dir / "2026-10-11"
    day_dir.mkdir(parents=True)
    good = _record("planner.decision", _ms(1), record_id="evt-ok")
    with open(day_dir / "21.jsonl", "w", encoding="utf-8") as f:
        f.write("{broken json\n")
        f.write(json.dumps(good.model_dump()) + "\n")

    window = SessionEventReader(events_dir=events_dir).collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert [r.id for r in window.records] == ["evt-ok"]
    assert window.days_pruned is False


def test_collect_cursor_keeps_strictly_older(tmp_path: Path) -> None:
    events_dir = tmp_path / "events"
    _write_hour_file(
        events_dir,
        "2026-10-11",
        21,
        [
            _record("planner.decision", _ms(1), record_id="a"),
            _record("planner.verdict", _ms(2), record_id="b"),
        ],
    )
    window = SessionEventReader(events_dir=events_dir).collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
        before_timestamp_ms=_ms(2),
    )
    assert [r.id for r in window.records] == ["a"]


def test_collect_without_any_source_returns_empty() -> None:
    """诊断未装配且无环形缓冲（极简启动）：空窗口、不报裁剪。"""
    window = SessionEventReader().collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert window.records == []
    assert window.days_pruned is False


def test_collect_dedupes_records_without_id(tmp_path: Path) -> None:
    """id 为空的记录退回 type@timestamp_ms 键去重。"""
    history = EventHistoryService(max_events=10)
    buffered = _record("streamer.stage", _ms(5), record_id="")
    history.record(buffered)
    events_dir = tmp_path / "events"
    _write_hour_file(events_dir, "2026-10-11", 21, [buffered])

    window = SessionEventReader(event_history=history, events_dir=events_dir).collect(
        live_session_id=7,
        started_at_ms=_START_MS,
        ended_at_ms=_END_MS,
    )
    assert len(window.records) == 1
