"""DiagnosticsWriter：小时流落盘、快照覆写与合并、过期清理、停机冲刷。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.modules.diagnostics.writer import DiagnosticsWriter, _is_date_dir_name


def _record(ts_ms: int, request_id: str = "req_x") -> dict:
    return {"request_id": request_id, "timestamp_ms": ts_ms, "request_params": {"messages": []}}


def _writer(base_dir: Path, retention_days: int = 0) -> DiagnosticsWriter:
    return DiagnosticsWriter(base_dir=base_dir, retention_days=retention_days)


def test_append_writes_a_day_and_hour_jsonl_stream_the_reader_can_query_by_request_id(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    ts_ms = int(datetime(2026, 10, 11, 14, 30, 0).timestamp() * 1000)
    writer.append("llm_requests", _record(ts_ms, "req_a"))
    writer.stop()

    stream = tmp_path / "llm_requests" / "2026-10-11" / "14.jsonl"
    assert json.loads(stream.read_text(encoding="utf-8").strip())["request_id"] == "req_a"
    assert writer.stream_path("llm_requests", ts_ms) == stream


def test_record_without_a_timestamp_is_stamped_with_now_instead_of_crashing(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.append("events", {"type": "core.startup"})
    writer.stop()

    (stream,) = (tmp_path / "events").rglob("*.jsonl")
    assert json.loads(stream.read_text(encoding="utf-8").strip())["type"] == "core.startup"


def test_snapshot_overwrite_leaves_no_tmp_file_and_the_read_modify_write_merge_flow_shows_both_models(
    tmp_path: Path,
) -> None:
    first = _writer(tmp_path)
    first.write_snapshot("llm_context", "latest.json", {"models": {"m1": {"tokens": 1}}})
    first.stop()
    assert not list((tmp_path / "llm_context").glob("*.tmp"))
    assert first.read_snapshot("llm_context", "latest.json")["models"]["m1"] == {"tokens": 1}

    second = _writer(tmp_path)  # 模拟 engine 的合并路径：读旧快照 → 改写 → 覆写
    models = dict(second.read_snapshot("llm_context", "latest.json").get("models", {}))
    models["m2"] = {"tokens": 2}
    second.write_snapshot("llm_context", "latest.json", {"models": models})
    second.stop()

    assert set(second.read_snapshot("llm_context", "latest.json")["models"]) == {"m1", "m2"}


@pytest.mark.parametrize(("retention_days", "old_kept"), [(14, False), (0, True)])
def test_cleanup_removes_expired_day_dirs_only_and_zero_retention_disables_it(
    tmp_path: Path, retention_days: int, old_kept: bool
) -> None:
    old_dir = tmp_path / "llm_requests" / (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    fresh_dir = tmp_path / "llm_requests" / datetime.now().strftime("%Y-%m-%d")
    for directory in (old_dir, fresh_dir):
        directory.mkdir(parents=True)
        (directory / "10.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (tmp_path / "llm_context").mkdir()
    (tmp_path / "llm_context" / "latest.json").write_text("{}", encoding="utf-8")

    writer = _writer(tmp_path, retention_days)
    writer.start()
    writer.stop()

    assert old_dir.exists() is old_kept  # 超期天目录整体删除
    assert fresh_dir.exists() and (tmp_path / "llm_context" / "latest.json").is_file()  # 未过期目录与快照保留


def test_stop_flushes_queued_records_and_later_appends_are_dropped_silently(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    ts_ms = int(time.time() * 1000)
    for index in range(5):
        writer.append("llm_requests", _record(ts_ms, f"req_{index}"))
    writer.stop()  # 入队后立即 stop，记录仍落盘

    (stream,) = (tmp_path / "llm_requests").rglob("*.jsonl")
    assert len(stream.read_text(encoding="utf-8").strip().splitlines()) == 5

    writer.append("events", {"type": "x"})  # stop 后再 append：进程退出竞态防御，静默丢弃
    assert not (tmp_path / "events").exists()


def test_date_dir_name_check_rejects_everything_but_real_dates() -> None:
    """防误删：只有合法日期目录名才会被清理。"""
    assert _is_date_dir_name("2026-10-11")
    assert not any(_is_date_dir_name(name) for name in ("2026-13-99", "latest.json", "app", "20261011"))
