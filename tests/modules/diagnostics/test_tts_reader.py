"""TTS 诊断记录读取：按场次时间跨度定位小时文件，取出每句台词的真实播放时段。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.modules.diagnostics.tts_reader import TtsDiagnosticsReader, TtsPlaybackWindow
from src.modules.diagnostics.tts_recorder import TTS_DIAGNOSTICS_KIND
from src.modules.diagnostics.writer import DiagnosticsWriter
from tests.modules.diagnostics.helpers import append_tts_record

# 本地时间 2026-11-24 23:50：场次从深夜跨到次日，记录分在两个小时的文件里
_STARTED_MS = int(datetime(2026, 11, 24, 23, 50).timestamp() * 1000)


def test_read_session_collects_finished_records_across_midnight(tmp_path: Path) -> None:
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    append_tts_record(
        writer,
        event="tts.utterance.finished",
        utterance_id="utt_a",
        live_session_id=7,
        timestamp_ms=_STARTED_MS + 9000,
        duration_ms=4000,
    )
    append_tts_record(
        writer,
        event="tts.utterance.finished",
        utterance_id="utt_b",
        live_session_id=7,
        timestamp_ms=_STARTED_MS + 3600_000 + 5000,
        duration_ms=6000,
    )
    append_tts_record(
        writer,
        event="tts.utterance.failed",
        utterance_id="utt_c",
        live_session_id=7,
        timestamp_ms=_STARTED_MS + 5000,
        error_message="合成失败",
    )
    append_tts_record(
        writer,
        event="tts.utterance.finished",
        utterance_id="utt_other",
        live_session_id=8,
        timestamp_ms=_STARTED_MS + 7000,
        duration_ms=3000,
    )

    windows = TtsDiagnosticsReader(writer).read_session(
        live_session_id=7, started_at_ms=_STARTED_MS, ended_at_ms=_STARTED_MS + 3660_000
    )

    # 播放时段 = 结束时刻往前推实际播放时长；失败的和别的场次的不算
    assert windows == {
        "utt_a": TtsPlaybackWindow(utterance_id="utt_a", started_ms=_STARTED_MS + 5000, ended_ms=_STARTED_MS + 9000),
        "utt_b": TtsPlaybackWindow(
            utterance_id="utt_b", started_ms=_STARTED_MS + 3600_000 - 1000, ended_ms=_STARTED_MS + 3600_000 + 5000
        ),
    }


def test_read_session_skips_broken_lines_and_missing_files(tmp_path: Path) -> None:
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    append_tts_record(
        writer,
        event="tts.utterance.finished",
        utterance_id="utt_a",
        live_session_id=7,
        timestamp_ms=_STARTED_MS + 9000,
        duration_ms=4000,
    )
    with open(writer.stream_path(TTS_DIAGNOSTICS_KIND, _STARTED_MS + 9000), "a", encoding="utf-8") as handle:
        handle.write("{这不是一行 JSON\n")
        handle.write(
            json.dumps(
                {
                    "event": "tts.utterance.finished",
                    "utterance_id": "utt_b",
                    "live_session_id": 7,
                    "timestamp_ms": 12_000,
                }
            )
            + "\n"
        )
    reader = TtsDiagnosticsReader(writer)

    # 坏行与缺播放时长的行跳过；该时段的小时文件不存在按没有记录处理
    assert set(reader.read_session(live_session_id=7, started_at_ms=_STARTED_MS, ended_at_ms=_STARTED_MS + 60_000)) == {
        "utt_a"
    }
    assert (
        reader.read_session(
            live_session_id=7, started_at_ms=_STARTED_MS + 7_200_000, ended_at_ms=_STARTED_MS + 7_260_000
        )
        == {}
    )
