"""LLM 诊断读侧：经 writer 版式出口回查请求正文与每模型水位快照。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.llm.readers import read_latest_breakdowns, read_llm_request_payload


def _writer(base_dir: Path) -> DiagnosticsWriter:
    return DiagnosticsWriter(base_dir=base_dir, retention_days=0)


def test_reader_roundtrip_locates_stream_via_the_writer_layout(tmp_path: Path) -> None:
    """写入一条正文后，读侧经同一 writer 实例按 request_id 回查命中。"""
    writer = _writer(tmp_path)
    ts_ms = int(datetime(2026, 10, 11, 14, 30, 0).timestamp() * 1000)
    writer.append("llm_requests", {"request_id": "req_a", "timestamp_ms": ts_ms, "request_params": {"messages": []}})
    writer.stop()

    assert read_llm_request_payload("req_a", ts_ms, writer)["request_id"] == "req_a"


def test_reader_returns_none_for_missing_file_or_writer(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    assert read_llm_request_payload("req_a", 1760000000000, writer) is None
    # writer 未装配（极简启动）返回 None，不猜测落盘目录
    assert read_llm_request_payload("req_a", 1760000000000, None) is None


def test_latest_breakdowns_snapshot_read_and_merge(tmp_path: Path) -> None:
    first = _writer(tmp_path)
    first.write_snapshot("llm_context", "latest.json", {"models": {"m1": {"tokens": 1}}})
    first.stop()
    assert read_latest_breakdowns(first)["m1"] == {"tokens": 1}

    second = _writer(tmp_path)  # 模拟 engine 的合并路径：读旧快照 → 改写 → 覆写
    models = dict(second.read_snapshot("llm_context", "latest.json").get("models", {}))
    models["m2"] = {"tokens": 2}
    second.write_snapshot("llm_context", "latest.json", {"models": models})
    second.stop()

    assert set(read_latest_breakdowns(second)) == {"m1", "m2"}


def test_latest_breakdowns_missing_or_unassembled_writer_returns_empty(tmp_path: Path) -> None:
    assert read_latest_breakdowns(_writer(tmp_path)) == {}
    assert read_latest_breakdowns(None) == {}
