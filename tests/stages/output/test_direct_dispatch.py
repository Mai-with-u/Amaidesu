"""OutputHandlerManager 直接派发行为测试。"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.pipeline import PipelineManager
from src.modules.types.intent import Intent, IntentMetadata
from src.stages.output.manager import OutputHandlerManager


def _make_payload(speech: str = "hello") -> IntentPayload:
    intent = Intent(
        speech=speech,
        metadata=IntentMetadata(
            source_id="direct-dispatch-test",
            decision_time_ms=1700000000000,
        ),
    )
    return IntentPayload.from_intent(intent, name="test_decider")  # type: ignore[attr-defined]


def _make_manager(
    bus: EventBus,
    handlers: list[Any],
    *,
    render_timeout_ms: int = 1000,
) -> OutputHandlerManager:
    pipeline_manager = MagicMock(spec=PipelineManager)
    pipeline_manager.process = AsyncMock(side_effect=lambda intent: intent)
    manager = OutputHandlerManager(
        event_bus=bus,
        pipeline_manager=pipeline_manager,
        config={"render_timeout_ms": render_timeout_ms},
    )
    for handler in handlers:
        manager.handlers.append(handler)
        manager._handler_names[handler] = type(handler).__name__
        manager._handler_started[handler] = True
    return manager


class _FinishedRecorder:
    def __init__(self) -> None:
        self.payloads: list[IntentPayload] = []
        self.received = asyncio.Event()

    async def callback(self, event_name: str, payload: IntentPayload, source: str) -> None:
        self.payloads.append(payload)
        self.received.set()


@pytest.mark.asyncio
async def test_single_handler_direct_call() -> None:
    bus = EventBus()
    called = asyncio.Event()
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        called.set()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler])
    payload = _make_payload()

    await manager._on_decision_intent(CoreEvents.DECISION_INTENT_GENERATED, payload, "test")
    await asyncio.wait_for(called.wait(), timeout=1.0)

    handler.handle.assert_awaited_once_with(payload.to_intent())  # type: ignore[attr-defined]
    assert bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED) == 0


@pytest.mark.asyncio
async def test_error_isolation() -> None:
    bus = EventBus()
    finished = _FinishedRecorder()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, finished.callback, model_class=IntentPayload)

    failing_handler = MagicMock()
    failing_handler.handle = AsyncMock(side_effect=RuntimeError("boom"))
    healthy_handler = MagicMock()
    healthy_handler.handle = AsyncMock(return_value=None)
    manager = _make_manager(bus, [failing_handler, healthy_handler])

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(finished.received.wait(), timeout=1.0)

    healthy_handler.handle.assert_awaited_once()
    assert len(finished.payloads) == 1


@pytest.mark.asyncio
async def test_timeout_enforced() -> None:
    bus = EventBus()
    finished = _FinishedRecorder()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, finished.callback, model_class=IntentPayload)

    handler = MagicMock()

    async def hang(intent: Intent) -> None:
        await asyncio.sleep(5.0)

    handler.handle = AsyncMock(side_effect=hang)
    manager = _make_manager(bus, [handler], render_timeout_ms=100)
    manager.logger.warning = MagicMock()

    started_at = time.monotonic()
    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(finished.received.wait(), timeout=0.3)

    assert time.monotonic() - started_at < 0.2
    assert any("timed out" in str(call) for call in manager.logger.warning.call_args_list)


@pytest.mark.asyncio
async def test_shutdown_does_not_cancel_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    finished = _FinishedRecorder()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, finished.callback, model_class=IntentPayload)
    started = asyncio.Event()
    completed = asyncio.Event()
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        started.set()
        await asyncio.sleep(2.0)
        completed.set()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler], render_timeout_ms=0)

    real_create_task = asyncio.create_task
    created_tasks: dict[str, list[asyncio.Task[Any]]] = {}

    def tracking_create_task(coro: Any, *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
        task = real_create_task(coro, *args, **kwargs)
        coroutine_name = getattr(getattr(coro, "cr_code", None), "co_name", "unknown")
        created_tasks.setdefault(coroutine_name, []).append(task)
        return task

    monkeypatch.setattr("src.stages.output.manager.asyncio.create_task", tracking_create_task)

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.sleep(0.2)

    gather_task = created_tasks["_gather_and_finalize"][0]
    handler_task = created_tasks["_run_handler"][0]
    gather_task.cancel()
    await asyncio.wait_for(gather_task, timeout=3.0)

    assert not handler_task.cancelled()
    assert handler_task.done()
    await asyncio.wait_for(completed.wait(), timeout=2.0)
    await asyncio.wait_for(finished.received.wait(), timeout=1.0)
    assert len(finished.payloads) == 1


@pytest.mark.asyncio
async def test_dispatched_still_emitted_as_monitoring() -> None:
    order: list[str] = []

    class RecordingEventBus(EventBus):
        async def emit(
            self,
            event_name: str,
            data: BaseModel,
            source: str = "unknown",
            error_isolate: bool = True,
            wait: bool = False,
        ) -> None:
            order.append(event_name)
            await super().emit(event_name, data, source, error_isolate, wait)

    bus = RecordingEventBus()
    handled = asyncio.Event()
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        order.append("handler.handle")
        handled.set()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler])

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(handled.wait(), timeout=1.0)

    assert CoreEvents.OUTPUT_INTENT_DISPATCHED in order
    assert order.index(CoreEvents.OUTPUT_INTENT_DISPATCHED) < order.index("handler.handle")


@pytest.mark.asyncio
async def test_zero_timeout_means_no_timeout() -> None:
    bus = EventBus()
    finished = _FinishedRecorder()
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, finished.callback, model_class=IntentPayload)
    completed = asyncio.Event()
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        await asyncio.sleep(0.5)
        completed.set()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler], render_timeout_ms=0)
    manager.logger.warning = MagicMock()

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(finished.received.wait(), timeout=1.0)

    assert completed.is_set()
    assert not any("timed out" in str(call) for call in manager.logger.warning.call_args_list)
