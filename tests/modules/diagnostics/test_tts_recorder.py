"""TtsDiagnosticsRecorder 走事件总线：utterance 终态事件逐句落 JSONL 诊断记录。"""

from __future__ import annotations

import json
from pathlib import Path

from src.modules.diagnostics.tts_recorder import TTS_DIAGNOSTICS_KIND, TtsDiagnosticsRecorder
from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceFailedPayload, UtteranceFinishedPayload

_TS_MS = 1_700_000_000_000


def _read_records(base_dir: Path) -> list[dict]:
    """读回落盘的 TTS 诊断记录（writer.stop 已冲刷，逐行 JSON；剔除每事件随机生成的 id）。"""
    path = base_dir / TTS_DIAGNOSTICS_KIND
    records: list[dict] = []
    for file in sorted(path.rglob("*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            record.pop("id", None)  # BasePayload 的事件实例 id 每次随机生成，不参与断言
            records.append(record)
    return records


async def test_finished_and_failed_events_are_recorded_via_bus(tmp_path: Path) -> None:
    """订阅由 start() 挂载：总线发 finished / failed 各一条，落盘行带事件名与专属字段。"""
    bus = EventBus()
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    recorder = TtsDiagnosticsRecorder(event_bus=bus, writer=writer)
    await recorder.start()
    try:
        await bus.emit(
            CoreEvents.TTS_UTTERANCE_FINISHED,
            UtteranceFinishedPayload(utterance_id="utt_ok", engine="index_tts", duration_ms=4_000, timestamp_ms=_TS_MS),
            source="test",
        )
        await bus.emit(
            CoreEvents.TTS_UTTERANCE_FAILED,
            UtteranceFailedPayload(
                utterance_id="utt_bad",
                speech_text="这句话",
                engine="index_tts",
                error_message="合成超时",
                timestamp_ms=_TS_MS,
            ),
            source="test",
        )
        await bus.wait_until_drained()
    finally:
        await recorder.stop()
        writer.stop()  # 落盘走后台线程，停机冲刷后才能读文件

    assert _read_records(tmp_path) == [
        {
            "utterance_id": "utt_ok",
            "engine": "index_tts",
            "duration_ms": 4_000,
            "live_session_id": 0,
            "timestamp_ms": _TS_MS,
            "event": "tts.utterance.finished",
        },
        {
            "utterance_id": "utt_bad",
            "speech_text": "这句话",
            "engine": "index_tts",
            "error_message": "合成超时",
            "live_session_id": 0,
            "timestamp_ms": _TS_MS,
            "event": "tts.utterance.failed",
        },
    ]


async def test_stop_unsubscribes_so_later_events_are_not_recorded(tmp_path: Path) -> None:
    """stop 摘除订阅：之后到达的终态事件不再落记录。"""
    bus = EventBus()
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    recorder = TtsDiagnosticsRecorder(event_bus=bus, writer=writer)
    await recorder.start()
    await recorder.stop()

    await bus.emit(
        CoreEvents.TTS_UTTERANCE_FINISHED,
        UtteranceFinishedPayload(
            utterance_id="utt_after_stop", engine="index_tts", duration_ms=1_000, timestamp_ms=_TS_MS
        ),
        source="test",
    )
    await bus.wait_until_drained()
    writer.stop()

    assert _read_records(tmp_path) == []
