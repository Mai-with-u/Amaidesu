"""事件查询面（EventQueryReader）测试：内存环形 ∪ 诊断文件合并。

覆盖：环形-only 翻页、文件年代补扫、take=oldest 反向取页、before 游标
跨文件→环形边界、天目录缺失降级信号（files_pruned）、坏行跳过、
类型/级别筛选。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.modules.dashboard.services.event_query import EventQueryReader
from src.modules.events.event_history import EventHistoryService, EventRecord

_DAY = "2026-10-17"


def _ms(hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 10, 17, hour, minute).timestamp() * 1000)


def _record(ts_ms: int, event_type: str = "room.message", level: str = "info") -> EventRecord:
    return EventRecord(type=event_type, timestamp_ms=ts_ms, source="test", level=level)


def _write_hour_file(events_dir: Path, day: str, hour: int, records: List[EventRecord]) -> None:
    day_dir = events_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    with open(day_dir / f"{hour:02d}.jsonl", "w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def _ids(records: List[EventRecord]) -> List[str]:
    return [record.id for record in records]


def _build_reader(
    tmp_path: Path,
    ring_records: Optional[List[EventRecord]],
    file_records: Optional[List[EventRecord]],
) -> tuple[EventQueryReader, List[str]]:
    """装配读取面：环形缓冲（可空）+ 诊断文件（17 日 8 时，可空）。返回 (reader, 文件记录 id)。"""
    history = None
    if ring_records is not None:
        history = EventHistoryService(max_events=100)
        for record in ring_records:
            history.record(record)
    file_ids: List[str] = []
    events_dir: Optional[Path] = None
    if file_records is not None:
        events_dir = tmp_path / "events"
        _write_hour_file(events_dir, _DAY, 8, file_records)
        file_ids = [record.id for record in file_records]
    return EventQueryReader(history, events_dir), file_ids


def test_ring_only_newest_page_and_has_more(tmp_path: Path) -> None:
    ring = [_record(_ms(10, m)) for m in range(5)]
    reader, _ = _build_reader(tmp_path, ring, None)

    window = reader.query(start_ms=_ms(9), end_ms=_ms(12), limit=3)

    assert _ids(window.records) == _ids(ring[-3:])
    assert window.has_more_older is True
    assert window.has_more_newer is False


def test_ring_exact_page_probes_files_for_older(tmp_path: Path) -> None:
    ring = [_record(_ms(10, m)) for m in range(3)]
    files = [_record(_ms(8, m)) for m in range(2)]
    reader, _ = _build_reader(tmp_path, ring, files)

    # 环形恰好一页：更早命中在文件年代，探测应发现并置 has_more_older
    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), limit=3)
    assert _ids(window.records) == _ids(ring)
    assert window.has_more_older is True

    # 窗口从环形最老记录开始：文件年代在窗外，无更早
    window = reader.query(start_ms=_ms(10), end_ms=_ms(12), limit=3)
    assert window.has_more_older is False


def test_file_era_fills_page_when_ring_below_limit(tmp_path: Path) -> None:
    ring = [_record(_ms(10, m)) for m in range(2)]
    files = [_record(_ms(8, m)) for m in range(4)]
    reader, file_ids = _build_reader(tmp_path, ring, files)

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), limit=4)

    # 最靠新的一页 = 环形 2 条 + 文件中最靠新的 2 条，整体升序
    assert _ids(window.records) == file_ids[2:] + _ids(ring)
    assert window.has_more_older is True  # 文件里还有更早的 2 条


def test_take_oldest_starts_at_window_start(tmp_path: Path) -> None:
    ring = [_record(_ms(10, m)) for m in range(3)]
    files = [_record(_ms(8, m)) for m in range(4)]
    reader, file_ids = _build_reader(tmp_path, ring, files)

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), take="oldest", limit=3)

    assert _ids(window.records) == file_ids[:3]
    assert window.has_more_newer is True
    assert window.has_more_older is False


def test_before_cursor_walks_across_file_ring_boundary(tmp_path: Path) -> None:
    ring = [_record(_ms(10, m)) for m in range(2)]
    files = [_record(_ms(8, m)) for m in range(3)]
    reader, file_ids = _build_reader(tmp_path, ring, files)

    # 游标 09:00：环形全被游标挡住，只剩文件年代最靠新的 2 条
    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), before_timestamp_ms=_ms(9), limit=2)
    assert _ids(window.records) == file_ids[1:]
    assert window.has_more_older is True

    # 继续翻：游标到文件第一条 → 空页，到窗口起点
    first_file_ts = files[0].timestamp_ms
    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), before_timestamp_ms=first_file_ts, limit=2)
    assert window.records == []
    assert window.has_more_older is False


def test_missing_day_dir_reports_pruned(tmp_path: Path) -> None:
    ring = [_record(_ms(10))]
    history = EventHistoryService(max_events=100)
    for record in ring:
        history.record(record)
    events_dir = tmp_path / "events"
    events_dir.mkdir()  # 诊断根目录存在，但 17 日天目录缺失
    reader = EventQueryReader(history, events_dir)

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), limit=5)

    assert window.files_pruned is True
    assert _ids(window.records) == _ids(ring)


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    day_dir = tmp_path / "events" / _DAY
    day_dir.mkdir(parents=True)
    with open(day_dir / "08.jsonl", "w", encoding="utf-8") as f:
        f.write("garbage\n")
        f.write(json.dumps({"type": "room.message"}) + "\n")  # 缺必需字段
        good = _record(_ms(8, 30))
        f.write(good.model_dump_json() + "\n")
    reader = EventQueryReader(None, tmp_path / "events")

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), limit=5)

    assert _ids(window.records) == [good.id]


def test_type_and_level_filters(tmp_path: Path) -> None:
    files = [
        _record(_ms(8), event_type="room.message"),
        _record(_ms(8, 30), event_type="planner.decision"),
        _record(_ms(8, 45), event_type="core.error", level="error"),
    ]
    reader, _ = _build_reader(tmp_path, None, files)

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), types=["planner.decision"], limit=10)
    assert len(window.records) == 1
    assert window.records[0].type == "planner.decision"

    window = reader.query(start_ms=_ms(7), end_ms=_ms(12), level="error", limit=10)
    assert len(window.records) == 1
    assert window.records[0].level == "error"
