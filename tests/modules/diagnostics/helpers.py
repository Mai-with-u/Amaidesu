"""TTS 诊断记录的测试替身与读取口测试共用的落盘助手。"""

from __future__ import annotations

import json
from typing import Any

from src.modules.diagnostics.tts_recorder import TTS_DIAGNOSTICS_KIND
from src.modules.diagnostics.writer import DiagnosticsWriter


def append_tts_record(writer: DiagnosticsWriter, **record: Any) -> None:
    """按落盘器的文件版式直接追加一条 TTS 诊断记录（不走后台线程，写完即落盘）。"""
    path = writer.stream_path(TTS_DIAGNOSTICS_KIND, int(record["timestamp_ms"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


__all__ = ["append_tts_record"]
