"""OutputHandlerManager 直接派发的补充并发与空集合测试。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.pipeline import PipelineManager
from src.modules.types.intent import Intent, IntentMetadata
from src.stages.output.manager import OutputHandlerManager


def _make_payload(speech: str) -> IntentPayload:
    intent = Intent(
        speech=speech,
        metadata=IntentMetadata(source_id="test", decision_time_ms=1700000000000),
    )
    return IntentPayload.from_intent(intent, name="test_decider")  # type: ignore[attr-defined]


def _make_manager(bus: EventBus, handlers: list[Any]) -> OutputHandlerManager:
    pipeline_manager = MagicMock(spec=PipelineManager)
    pipeline_manager.process = AsyncMock(side_effect=lambda intent: intent)
    manager = OutputHandlerManager(bus, pipeline_manager, config={"render_timeout_ms": 1000})
    for handler in handlers:
        manager.handlers.append(handler)
        manager._handler_names[handler] = type(handler).__name__
        manager._handler_started[handler] = True
    return manager


@pytest.mark.asyncio
async def test_no_active_handlers_dispatches_then_finishes() -> None:
    bus = EventBus()
    order: list[str] = []
    finished = asyncio.Event()

    async def on_dispatched(event_name: str, payload: IntentPayload, source: str) -> None:
        order.append(event_name)

    async def on_finished(event_name: str, payload: IntentPayload, source: str) -> None:
        order.append(event_name)
        finished.set()

    bus.on(CoreEvents.OUTPUT_INTENT_DISPATCHED, on_dispatched, model_class=IntentPayload)
    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, on_finished, model_class=IntentPayload)
    manager = _make_manager(bus, [])

    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload("empty"),
        "test",
    )
    await asyncio.wait_for(finished.wait(), timeout=1.0)

    assert order == [CoreEvents.OUTPUT_INTENT_DISPATCHED, CoreEvents.OUTPUT_INTENT_FINISHED]


@pytest.mark.asyncio
async def test_inactive_handlers_are_not_called() -> None:
    bus = EventBus()
    active = MagicMock()
    active.handle = AsyncMock(return_value=None)
    inactive = MagicMock()
    inactive.handle = AsyncMock(return_value=None)
    manager = _make_manager(bus, [active])
    manager.handlers.append(inactive)
    manager._handler_names[inactive] = "inactive"
    manager._handler_started[inactive] = False

    completed = asyncio.Event()

    async def on_finished(event_name: str, payload: IntentPayload, source: str) -> None:
        completed.set()

    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, on_finished, model_class=IntentPayload)
    await manager._on_decision_intent(
        CoreEvents.DECISION_INTENT_GENERATED,
        _make_payload("active-only"),
        "test",
    )
    await asyncio.wait_for(completed.wait(), timeout=1.0)

    active.handle.assert_awaited_once()
    inactive.handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_intents_finalize_independently() -> None:
    bus = EventBus()
    release_by_speech = {"A": asyncio.Event(), "B": asyncio.Event()}
    handler = MagicMock()

    async def handle(intent: Intent) -> None:
        assert intent.speech is not None
        await release_by_speech[intent.speech].wait()

    handler.handle = AsyncMock(side_effect=handle)
    manager = _make_manager(bus, [handler])
    finished_speeches: list[str] = []
    first_finished = asyncio.Event()
    all_finished = asyncio.Event()

    async def on_finished(event_name: str, payload: IntentPayload, source: str) -> None:
        intent = payload.to_intent()  # type: ignore[attr-defined]
        finished_speeches.append(intent.speech)
        first_finished.set()
        if len(finished_speeches) == 2:
            all_finished.set()

    bus.on(CoreEvents.OUTPUT_INTENT_FINISHED, on_finished, model_class=IntentPayload)

    await asyncio.gather(
        manager._on_decision_intent(
            CoreEvents.DECISION_INTENT_GENERATED,
            _make_payload("A"),
            "test",
        ),
        manager._on_decision_intent(
            CoreEvents.DECISION_INTENT_GENERATED,
            _make_payload("B"),
            "test",
        ),
    )
    release_by_speech["B"].set()
    await asyncio.wait_for(first_finished.wait(), timeout=1.0)
    assert finished_speeches == ["B"]

    release_by_speech["A"].set()
    await asyncio.wait_for(all_finished.wait(), timeout=1.0)
    assert finished_speeches == ["B", "A"]
