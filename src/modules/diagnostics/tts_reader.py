"""TTS 诊断记录的读取口：按场次读出每句台词的真实播放时段。

写侧是 ``TtsDiagnosticsRecorder``，逐句把 ``finished`` / ``failed`` 记录追加到小时
文件；这里按场次的时间跨度定位同一条流，取出 ``finished`` 记录里的播放时长与
结束时刻，换算成每句的出声与播完时刻。需要真实出声时刻的场合（切片定起止）
用它代替估算；记录缺失（过了保留期被清理、当时没开）时调用方自行退回估算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from src.modules.diagnostics.tts_recorder import TTS_DIAGNOSTICS_KIND
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.diagnostics.writer import DiagnosticsWriter

logger = get_logger("TtsDiagnosticsReader")

_FINISHED_EVENT = "tts.utterance.finished"
_HOUR_MS = 3_600_000
# 一次读取最多覆盖的小时数（场次时间跨度异常时不去扫整个磁盘）
_MAX_HOURS = 72


@dataclass(frozen=True)
class TtsPlaybackWindow:
    """一句台词的真实播放时段（Unix 毫秒）。"""

    utterance_id: str
    started_ms: int
    ended_ms: int


class TtsDiagnosticsReader:
    """按场次读 TTS 诊断记录；文件版式经落盘器的 ``stream_path`` 定位。"""

    def __init__(self, writer: "DiagnosticsWriter") -> None:
        self._writer = writer

    def read_session(
        self, *, live_session_id: int, started_at_ms: int, ended_at_ms: int
    ) -> Dict[str, TtsPlaybackWindow]:
        """场次时间跨度覆盖的各小时文件里属于该场的 ``finished`` 记录，按发声编号索引。

        文件不存在、某行不是合法 JSON 或缺字段都跳过：读不到只说明这一句没有真实记录。
        """
        windows: Dict[str, TtsPlaybackWindow] = {}
        for path in self._stream_files(started_at_ms, ended_at_ms):
            for record in _read_records(path):
                window = _window_of(record, live_session_id=live_session_id)
                if window is not None:
                    windows[window.utterance_id] = window
        return windows

    def _stream_files(self, started_at_ms: int, ended_at_ms: int) -> List[Path]:
        """场次起止之间每个小时对应的记录文件，只返回存在的。

        开场时刻逐小时往后推，每个代表时刻的分秒相同、落在逐个小时里；时刻到
        文件位置的换算全部交给落盘器的 ``stream_path``。
        """
        span_hours = (max(started_at_ms, ended_at_ms) - started_at_ms) // _HOUR_MS
        paths: List[Path] = []
        for offset in range(min(span_hours + 1, _MAX_HOURS)):
            path = self._writer.stream_path(TTS_DIAGNOSTICS_KIND, started_at_ms + offset * _HOUR_MS)
            if path.is_file():
                paths.append(path)
        return paths


def _read_records(path: Path) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        logger.warning(f"TTS 诊断记录读取失败，跳过该文件：{path}（{exc}）")
        return []
    records: List[Dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _window_of(record: Dict[str, Any], *, live_session_id: int) -> "TtsPlaybackWindow | None":
    if record.get("event") != _FINISHED_EVENT or record.get("live_session_id") != live_session_id:
        return None
    utterance_id = record.get("utterance_id")
    ended_ms = record.get("timestamp_ms")
    duration_ms = record.get("duration_ms")
    if not isinstance(utterance_id, str) or not utterance_id:
        return None
    if not isinstance(ended_ms, int) or not isinstance(duration_ms, int) or duration_ms < 0:
        return None
    return TtsPlaybackWindow(utterance_id=utterance_id, started_ms=ended_ms - duration_ms, ended_ms=ended_ms)


__all__ = ["TtsDiagnosticsReader", "TtsPlaybackWindow"]
