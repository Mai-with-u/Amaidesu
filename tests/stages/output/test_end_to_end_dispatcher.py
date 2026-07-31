"""OutputHandlerManager 直接派发端到端回归测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.pipeline import PipelineManager
from src.modules.types.intent import Intent, IntentMetadata
from src.stages.output.manager import OutputHandlerManager


def _make_payload(name: str = "test_decider") -> IntentPayload:
    intent = Intent(
        speech="Hello world",
        metadata=IntentMetadata(
            source_id="test",
            decision_time_ms=1729612345678,
        ),
    )
    return IntentPayload.from_intent(intent, name=name)  # type: ignore[attr-defined]


def _make_manager(bus: EventBus, handlers: list[MagicMock]) -> OutputHandlerManager:
    pipeline_manager = MagicMock(spec=PipelineManager)
    pipeline_manager.process = AsyncMock(side_effect=lambda intent: intent)
    manager = OutputHandlerManager(bus, pipeline_manager)
    for index, handler in enumerate(handlers):
        manager.handlers.append(handler)
        manager._handler_names[handler] = f"handler-{index}"
        manager._handler_started[handler] = True
    return manager


@pytest.mark.asyncio
async def test_dispatcher_triggers_all_seven_handlers() -> None:
    bus = EventBus()
    handlers = [MagicMock() for _ in range(7)]
    for handler in handlers:
        handler.handle = AsyncMock(return_value=None)
    manager = _make_manager(bus, handlers)
    finished = asyncio.Event()

    async def on_finished(event_name: str, payload: IntentPayload, source: str) -> None:
        finished.set()

    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, on_finished, model_class=IntentPayload)
    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(finished.wait(), timeout=1.0)

    for handler in handlers:
        handler.handle.assert_awaited_once()
    assert bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED) == 0


@pytest.mark.asyncio
async def test_dispatcher_emits_monitoring_payload() -> None:
    bus = EventBus()
    handler = MagicMock()
    handler.handle = AsyncMock(return_value=None)
    manager = _make_manager(bus, [handler])
    received: list[IntentPayload] = []
    dispatched = asyncio.Event()

    async def on_dispatched(event_name: str, payload: IntentPayload, source: str) -> None:
        received.append(payload)
        dispatched.set()

    bus.on(CoreEvents.OUTPUT_INTENT_DISPATCHED, on_dispatched, model_class=IntentPayload)
    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(dispatched.wait(), timeout=1.0)

    assert len(received) == 1
    assert received[0].name == "test_decider"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatcher_passes_typed_intent_to_handler() -> None:
    bus = EventBus()
    received: list[Intent] = []
    handled = asyncio.Event()
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        received.append(intent)
        handled.set()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler])

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload(),
        "test",
    )
    await asyncio.wait_for(handled.wait(), timeout=1.0)

    assert len(received) == 1
    intent = received[0]
    assert isinstance(intent, Intent)
    assert intent.speech == "Hello world"
    assert intent.metadata.decision_time_ms == 1729612345678
    assert intent.metadata.source_id == "test"
