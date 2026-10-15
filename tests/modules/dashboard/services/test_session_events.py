"""SessionEventReader：环形缓冲 ∪ 诊断文件合并 / 去重 / 白名单 / 游标 / 降级信号。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.modules.dashboard.services.session_events import SessionEventReader, is_replay_event_type
from src.modules.events.event_history import EventHistoryService, EventRecord

# 扫描范围按时间戳落在的本地日期枚举天目录，夹具统一用真实时刻
_DAY = datetime(2026, 10, 11, 21, 0, 0)
_START_MS = int(datetime(2026, 10, 11, 20, 0, 0).timestamp() * 1000)
_END_MS = int(datetime(2026, 10, 11, 23, 0, 0).timestamp() * 1000)


def _ms(minute: int) -> int:
    """场次日内第 minute 分钟对应的毫秒时刻（落在 2026-10-11 21 时文件）。"""
    return int(_DAY.replace(minute=minute).timestamp() * 1000)


def _record(type_: str, ts_ms: int, session_id: int = 7, record_id: str = "") -> EventRecord:
    return EventRecord(
        id=record_id,
        type=type_,
        event_name=type_,
        timestamp_ms=ts_ms,
        level="info",
        source=str(session_id),
        summary="s",
        data={"live_session_id": session_id},
    )


def _write_hour_file(events_dir: Path, records: list[EventRecord]) -> None:
    day_dir = events_dir / "2026-10-11"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "21.jsonl").write_text("".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8")


def _collect(reader: SessionEventReader, **kwargs):
    return reader.collect(live_session_id=7, started_at_ms=_START_MS, ended_at_ms=_END_MS, **kwargs)


def test_replay_whitelist_covers_exact_names_and_dynamic_prefixes() -> None:
    for name in ("planner.decision", "game.milestone", "tool.result.maicraft_observe"):
        assert is_replay_event_type(name)
    for name in ("room.message.danmaku", "llm.context.used", "task.changed", "core.startup"):
        assert not is_replay_event_type(name)


def test_collect_merges_buffer_and_files_dedupes_by_id_or_type_at_timestamp_and_sorts_ascending(tmp_path) -> None:
    history = EventHistoryService(max_events=100)
    buffered = _record("planner.decision", _ms(2), record_id="evt-1")
    no_id = _record("streamer.stage", _ms(5))  # id 为空：退回 type@timestamp_ms 键去重
    history.record(buffered)
    history.record(no_id)
    _write_hour_file(
        tmp_path / "events",
        [
            buffered,
            no_id,
            _record("planner.verdict", _ms(1), record_id="evt-0"),
            _record("streamer.stage", _ms(3), record_id="evt-2"),
        ],
    )

    window = _collect(SessionEventReader(event_history=history, events_dir=tmp_path / "events"))

    assert [r.timestamp_ms for r in window.records] == [_ms(1), _ms(2), _ms(3), _ms(5)]
    assert window.days_pruned is False


def test_collect_drops_other_sessions_and_events_outside_the_whitelist(tmp_path) -> None:
    _write_hour_file(
        tmp_path / "events",
        [
            _record("planner.decision", _ms(1)),
            _record("planner.decision", _ms(1) + 10, session_id=8),  # 他场
            _record("task.changed", _ms(2)),  # 非卡面
            _record("llm.context.used", _ms(3)),  # 非卡面
            _record("tool.result.minecraft_glance", _ms(4)),  # 动态族
        ],
    )

    window = _collect(SessionEventReader(events_dir=tmp_path / "events"))

    assert [r.type for r in window.records] == ["planner.decision", "tool.result.minecraft_glance"]


def test_collect_skips_malformed_lines_and_cursor_keeps_strictly_older(tmp_path) -> None:
    day_dir = tmp_path / "events" / "2026-10-11"
    day_dir.mkdir(parents=True)
    good = [_record("planner.decision", _ms(1), record_id="a"), _record("planner.verdict", _ms(2), record_id="b")]
    lines = ["{broken json"] + [json.dumps(record.model_dump()) for record in good]
    (day_dir / "21.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    reader = SessionEventReader(events_dir=tmp_path / "events")

    assert [r.id for r in _collect(reader).records] == ["a", "b"]  # 坏行跳过不抛错
    assert [r.id for r in _collect(reader, before_timestamp_ms=_ms(2)).records] == ["a"]


def test_collect_signals_pruned_days_only_when_a_day_dir_is_missing_and_empty_without_any_source(tmp_path) -> None:
    window = _collect(SessionEventReader(events_dir=tmp_path / "events"))
    assert window.records == [] and window.days_pruned is True  # 范围内天目录缺失：事件侧降级信号

    window = _collect(SessionEventReader())  # 诊断未装配且无环形缓冲（极简启动）：空窗口、不报裁剪
    assert window.records == [] and window.days_pruned is False
