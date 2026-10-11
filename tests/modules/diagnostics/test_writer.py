"""DiagnosticsWriter 单元测试：小时流落盘、快照覆写、过期清理、停机冲刷。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.modules.diagnostics.readers import read_latest_breakdowns, read_llm_request_payload
from src.modules.diagnostics.writer import DiagnosticsWriter, _is_date_dir_name


def _record(ts_ms: int, request_id: str = "req_x") -> dict:
    return {"request_id": request_id, "timestamp_ms": ts_ms, "request_params": {"messages": []}}


def test_append_writes_hourly_stream_file(tmp_path: Path) -> None:
    """append 按天/小时两级目录落 JSONL，读侧可按 request_id 回查。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    ts_ms = int(datetime(2026, 10, 11, 14, 30, 0).timestamp() * 1000)
    writer.append("llm_requests", _record(ts_ms, "req_a"))
    writer.stop()

    stream = tmp_path / "llm_requests" / "2026-10-11" / "14.jsonl"
    assert stream.is_file()
    line = json.loads(stream.read_text(encoding="utf-8").strip())
    assert line["request_id"] == "req_a"

    payload = read_llm_request_payload("req_a", ts_ms, base_dir=tmp_path)
    assert payload is not None and payload["request_id"] == "req_a"


def test_append_missing_timestamp_falls_back_to_now(tmp_path: Path) -> None:
    """缺 timestamp_ms 的记录以当前时刻兜底（不崩、有落盘）。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    writer.append("events", {"type": "core.startup"})
    writer.stop()

    files = list((tmp_path / "events").rglob("*.jsonl"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8").strip())["type"] == "core.startup"


def test_snapshot_atomic_overwrite_and_read(tmp_path: Path) -> None:
    """快照覆写不残留 tmp 文件，读侧拿到合并后的最新模型条目。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    writer.write_snapshot("llm_context", "latest.json", {"models": {"m1": {"tokens": 1}}})
    writer.stop()

    assert (tmp_path / "llm_context" / "latest.json").is_file()
    assert not list((tmp_path / "llm_context").glob("*.tmp"))
    models = read_latest_breakdowns(base_dir=tmp_path)
    assert models["m1"] == {"tokens": 1}


def test_snapshot_merge_flow(tmp_path: Path) -> None:
    """写侧合并流程：读旧快照 → 改写 → 覆写 → 读侧可见两模型。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    writer.write_snapshot("llm_context", "latest.json", {"models": {"m1": {"tokens": 1}}})
    writer.stop()

    # 模拟 engine 的合并路径
    writer2 = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    snapshot = writer2.read_snapshot("llm_context", "latest.json")
    models = dict(snapshot.get("models", {}))
    models["m2"] = {"tokens": 2}
    writer2.write_snapshot("llm_context", "latest.json", {"models": models})
    writer2.stop()

    models = read_latest_breakdowns(base_dir=tmp_path)
    assert set(models) == {"m1", "m2"}


def test_cleanup_expired_day_directories(tmp_path: Path) -> None:
    """超期天目录整体删除，快照文件与未过期目录保留。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=14)
    cutoff = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    fresh = datetime.now().strftime("%Y-%m-%d")

    old_dir = tmp_path / "llm_requests" / cutoff
    old_dir.mkdir(parents=True)
    (old_dir / "10.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    fresh_dir = tmp_path / "llm_requests" / fresh
    fresh_dir.mkdir(parents=True)
    (fresh_dir / "10.jsonl").write_text('{"x": 2}\n', encoding="utf-8")
    (tmp_path / "llm_context").mkdir()
    (tmp_path / "llm_context" / "latest.json").write_text("{}", encoding="utf-8")

    writer.start()
    writer.stop()

    assert not old_dir.exists()
    assert fresh_dir.is_file() or fresh_dir.is_dir()  # 未过期目录保留
    assert (tmp_path / "llm_context" / "latest.json").is_file()


def test_retention_zero_disables_cleanup(tmp_path: Path) -> None:
    """retention_days=0 关闭清理：过期目录保留。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    old_dir = tmp_path / "llm_requests" / "2020-01-01"
    old_dir.mkdir(parents=True)
    writer.start()
    writer.stop()
    assert old_dir.exists()


def test_stop_flushes_queued_records(tmp_path: Path) -> None:
    """stop 冲刷队列：入队后立即 stop，记录仍落盘。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    ts_ms = int(time.time() * 1000)
    for index in range(5):
        writer.append("llm_requests", _record(ts_ms, f"req_{index}"))
    writer.stop()

    stream = list((tmp_path / "llm_requests").rglob("*.jsonl"))[0]
    lines = [json.loads(line) for line in stream.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 5


def test_is_date_dir_name() -> None:
    """日期目录名判定：合法日期通过，非日期形态一律拒绝（防误删）。"""
    assert _is_date_dir_name("2026-10-11")
    assert not _is_date_dir_name("2026-13-99")
    assert not _is_date_dir_name("latest.json")
    assert not _is_date_dir_name("app")
    assert not _is_date_dir_name("20261011")


def test_append_after_stop_is_dropped_silently(tmp_path: Path) -> None:
    """stop 后再 append：静默丢弃不崩（进程退出竞态防御）。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    writer.start()
    writer.stop()
    writer.append("events", {"type": "x"})
    # 无异常即通过；不产生新文件
