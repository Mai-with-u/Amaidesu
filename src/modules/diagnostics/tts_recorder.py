"""TTS 逐句合成诊断记录：订阅 utterance 生命周期事件，逐句落 JSONL。

消费场景：合成质量巡检（回放工具按场次/时间段读文件）与发声链路排障。
每句一行（``finished`` / ``failed`` 终态；``started`` 不记——避免行数翻倍，
started→finished 的间隔可经 ``timestamp_ms`` 与 playback 记录对账）。

订阅侧接法零侵入 TTS 模块：``tts.utterance.*`` 是终点广播，本记录器只读
载荷不触发任何新决策（防环约束）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.diagnostics.writer import DiagnosticsWriter
    from src.modules.events.event_bus import EventBus

logger = get_logger("TtsDiagnosticsRecorder")

_KIND = "tts"


class TtsDiagnosticsRecorder:
    """订阅 ``tts.utterance.finished`` / ``failed``，逐句追加诊断记录。"""

    def __init__(self, event_bus: "EventBus", writer: "DiagnosticsWriter") -> None:
        self._event_bus = event_bus
        self._writer = writer

    async def start(self) -> None:
        """挂载两个终态事件的订阅。"""
        self._event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, self._on_finished, model_class=UtteranceFinishedPayload)
        self._event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, self._on_failed, model_class=UtteranceFailedPayload)
        logger.info("TTS 合成诊断记录已启动（订阅 finished/failed）")

    async def stop(self) -> None:
        """摘除订阅（停机序列调用；writer 停机由组合根统一负责）。"""
        self._event_bus.off(CoreEvents.TTS_UTTERANCE_FINISHED, self._on_finished)
        self._event_bus.off(CoreEvents.TTS_UTTERANCE_FAILED, self._on_failed)

    async def _on_finished(self, event_name: str, data: UtteranceFinishedPayload, source: str) -> None:
        self._append("tts.utterance.finished", data, {"duration_ms": data.duration_ms})

    async def _on_failed(self, event_name: str, data: UtteranceFailedPayload, source: str) -> None:
        self._append(
            "tts.utterance.failed",
            data,
            {"error_message": data.error_message},
        )

    def _append(self, event_name: str, data: BasePayload, extra: Dict[str, Any]) -> None:
        try:
            record: Dict[str, Any] = data.model_dump()
            record["event"] = event_name
            record.update(extra)
            self._writer.append(_KIND, record)
        except Exception as exc:  # noqa: BLE001 记录旁路，失败不传播
            logger.warning(f"TTS 诊断记录写入失败: {exc}")


__all__ = ["TtsDiagnosticsRecorder"]
