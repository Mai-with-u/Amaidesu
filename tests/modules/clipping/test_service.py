"""下播事件触发自动 AI 选段。

事件总线统一按 (事件名, 载荷, 事件源) 三参调用订阅者；本文件走真实事件总线
发出 ``live.ended``，断言切片流程真的启动（AI 选段任务登记完成、选段落库）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List

import pytest

from src.modules.background.registry import BackgroundTaskRegistry
from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.overrides import RecordingOverrideStore
from src.modules.clipping.service import ClippingService
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.background import BackgroundTaskChangedPayload
from src.modules.events.payloads.live import LiveEndedPayload
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from tests.modules.llm.fakes import FakeLLMManager

# 模型给出的选段：一段 60 秒（最短 30 秒、最长 300 秒的约束内）
_SELECTION_JSON = '[{"start_s": 60, "end_s": 120, "title": "开场寒暄", "reason": "弹幕互动"}]'

# 录播文件名里的时刻（本地时区），与录播目录扫描用的是同一套解析
_RECORDING_NAME = "2026-11-08 20-00-05.mkv"
_RECORDING_START_MS = int(datetime(2026, 11, 8, 20, 0, 5).timestamp() * 1000)


async def test_live_ended_starts_auto_selection(
    store: SQLiteDatabase,
    event_bus: EventBus,
    tmp_path: Path,
) -> None:
    """下播事件经事件总线发出后，自动 AI 选段启动并把选段落库。"""
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    (recording_dir / _RECORDING_NAME).write_bytes(b"x")

    session_id = await store.sessions.insert_live_session(started_at_ms=_RECORDING_START_MS, source="manual")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=_RECORDING_START_MS + 3600_000)

    registry = BackgroundTaskRegistry(event_bus)
    service = ClippingService(
        event_bus=event_bus,
        database=store,
        llm_manager=FakeLLMManager(response=Response(success=True, content=_SELECTION_JSON)),
        config=ClippingConfigSchema(enabled=True, recording_dir=str(recording_dir)),
        task_registry=registry,
        override_store=RecordingOverrideStore(tmp_path / "overrides.json"),
    )
    service.start()

    terminal: List[BackgroundTaskChangedPayload] = []
    finished = asyncio.Event()

    async def _on_task_changed(event_name: str, payload: BackgroundTaskChangedPayload, source: str) -> None:
        if payload.task_id != f"clip-select-{session_id}":
            return
        if payload.state in ("finished", "failed"):
            terminal.append(payload)
            finished.set()

    event_bus.on(CoreEvents.BACKGROUND_TASK_CHANGED, _on_task_changed, model_class=BackgroundTaskChangedPayload)

    await event_bus.emit(
        CoreEvents.LIVE_ENDED,
        LiveEndedPayload(live_session_id=session_id, reason="手动结束"),
        source="test",
    )

    try:
        await asyncio.wait_for(finished.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        pytest.fail("下播后没有出现 AI 选段任务：下播回调可能未被事件总线正确调用")

    assert terminal[0].state == "finished", f"AI 选段任务未成功：{terminal[0].failure_reason}"
    segments = await store.clips.list_session_segments(live_session_id=session_id)
    assert [segment.title for segment in segments] == ["开场寒暄"]
    assert [segment.origin for segment in segments] == ["ai"]


async def test_live_ended_skips_discarded_session(
    store: SQLiteDatabase,
    event_bus: EventBus,
    tmp_path: Path,
) -> None:
    """空场次被丢弃时下播回调不启动选段（场次行已删，无从选起）。"""
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    (recording_dir / _RECORDING_NAME).write_bytes(b"x")

    registry = BackgroundTaskRegistry(event_bus)
    service = ClippingService(
        event_bus=event_bus,
        database=store,
        llm_manager=FakeLLMManager(response=Response(success=True, content=_SELECTION_JSON)),
        config=ClippingConfigSchema(enabled=True, recording_dir=str(recording_dir)),
        task_registry=registry,
        override_store=RecordingOverrideStore(tmp_path / "overrides.json"),
    )
    service.start()

    tasks: List[BackgroundTaskChangedPayload] = []

    async def _on_task_changed(event_name: str, payload: BackgroundTaskChangedPayload, source: str) -> None:
        tasks.append(payload)

    event_bus.on(CoreEvents.BACKGROUND_TASK_CHANGED, _on_task_changed, model_class=BackgroundTaskChangedPayload)

    await event_bus.emit(
        CoreEvents.LIVE_ENDED,
        LiveEndedPayload(live_session_id=999, reason="手动结束", empty_discarded=True),
        source="test",
    )
    await event_bus.wait_until_drained()

    assert [payload.task_id for payload in tasks] == []
