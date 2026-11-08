"""归属人工覆盖存储（JSON 文件）的读写行为。"""

from __future__ import annotations

import json
from pathlib import Path

from src.modules.clipping.overrides import RecordingOverride, RecordingOverrideStore


def test_upsert_dedupes_by_key_and_last_wins(tmp_path: Path) -> None:
    store = RecordingOverrideStore(tmp_path / "overrides.json")
    assert store.load() == []  # 文件不存在 = 无覆盖

    store.upsert(RecordingOverride(path="E:\\rec\\a.flv", live_session_id=1, action="assign", start_ms=1000))
    store.upsert(RecordingOverride(path="E:\\rec\\a.flv", live_session_id=1, action="detach"))
    store.upsert(RecordingOverride(path="E:\\rec\\a.flv", live_session_id=1, action="assign", start_ms=2000))
    loaded = store.load()
    assert len(loaded) == 2  # 同 key 的 assign 只留最后一条
    assign = next(o for o in loaded if o.action == "assign")
    assert assign.start_ms == 2000


def test_for_session_and_drop(tmp_path: Path) -> None:
    store = RecordingOverrideStore(tmp_path / "overrides.json")
    store.upsert(RecordingOverride(path="a.flv", live_session_id=1, action="detach"))
    store.upsert(RecordingOverride(path="b.flv", live_session_id=2, action="assign", start_ms=5))
    assert [o.path for o in store.for_session(1)] == ["a.flv"]

    assert store.drop(RecordingOverride(path="a.flv", live_session_id=1, action="detach")) is True
    assert store.drop(RecordingOverride(path="a.flv", live_session_id=1, action="detach")) is False
    assert store.for_session(1) == []


def test_corrupt_file_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text("{不是json", encoding="utf-8")
    store = RecordingOverrideStore(path)
    assert store.load() == []
    # 且能正常写回（覆盖坏文件）
    store.upsert(RecordingOverride(path="c.flv", live_session_id=9, action="detach"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["overrides"][0]["path"] == "c.flv"
