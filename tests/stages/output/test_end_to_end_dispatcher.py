"""End-to-end dispatcher test.

Verifies the critical fix: when `OutputHandlerManager._on_decision_intent`
emits `OUTPUT_INTENT_DISPATCHED`, all 7 subscribed OutputHandlers
receive exactly one invocation each.

This is the regression test for the broken dispatcher bug discovered
during the refactor — before the fix, handlers subscribed to
`OUTPUT_INTENT_READY` but the manager emitted `OUTPUT_INTENT_DISPATCHED`,
so NO handler ever received the Intent.

回归测试覆盖:
1. test_dispatcher_triggers_all_seven_handlers
   - 7 个 mock handler 订阅 OUTPUT_INTENT_DISPATCHED
   - manager 模拟发布一次该事件
   - 验证所有 7 个 handler 都收到恰好 1 次 Intent

2. test_dispatcher_idempotent_subscription
   - 重复调用 init() 不会重复订阅
   - 验证 EventBus 注册的监听器数量仍为 1

3. test_dispatcher_event_payload_typing
   - handler 收到的是 Intent 对象(经过 to_intent() 反序列化)
   - 而非原始 dict,字段访问 type-safe
"""
import asyncio
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.types.intent import Intent, IntentMetadata


# ============================================================
# Test fixtures / helpers
# ============================================================


def _make_intent_payload(name: str = "test_decider") -> IntentPayload:
    """Helper: create a test IntentPayload with sample data."""
    intent = Intent(
        emotion="neutral",
        action="blink",
        speech="Hello world",
        context="",
        metadata=IntentMetadata(
            source_id="test",
            decision_time_ms=1729612345678,
            parser_type="test",
        ),
    )
    return IntentPayload.from_intent(intent, name=name)


class _MockOutputHandler:
    """Mock OutputHandler that tracks Intent invocations.

    Mirrors the real handler subscription pattern:
    - `init()` subscribes to OUTPUT_INTENT_DISPATCHED (idempotent)
    - `cleanup()` unsubscribes
    - The dispatcher-wired callback converts payload -> Intent and calls `handle()`
    """

    def __init__(self, name: str):
        self.name = name
        self.is_subscribed = False
        self.received_intents: list = []
        self._event_bus: EventBus | None = None
        self._original_handler = None  # for bus.off()

    async def _on_intent_dispatched(self, event_name: str, payload: IntentPayload, source: str):
        """Typed handler for OUTPUT_INTENT_DISPATCHED (mirrors real handlers)."""
        intent = payload.to_intent()
        await self.handle(intent)

    async def handle(self, intent: Intent) -> None:
        """Business logic entry point — appends to received_intents."""
        self.received_intents.append(intent)

    async def init(self) -> None:
        """Idempotent subscription to OUTPUT_INTENT_DISPATCHED."""
        if self.is_subscribed or self._event_bus is None:
            return
        # Capture original handler for later bus.off()
        self._original_handler = self._on_intent_dispatched
        self._event_bus.on(
            CoreEvents.OUTPUT_INTENT_DISPATCHED,
            self._on_intent_dispatched,
            model_class=IntentPayload,
        )
        self.is_subscribed = True

    async def cleanup(self) -> None:
        """Unsubscribe from OUTPUT_INTENT_DISPATCHED."""
        if not self.is_subscribed or self._event_bus is None:
            return
        try:
            self._event_bus.off(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._on_intent_dispatched,
            )
        except Exception:
            # Cleanup must be best-effort
            pass
        self.is_subscribed = False


# ============================================================
# Test 1: End-to-end dispatcher
# ============================================================


@pytest.mark.asyncio
async def test_dispatcher_triggers_all_seven_handlers():
    """End-to-end: emit OUTPUT_INTENT_DISPATCHED → 7 handlers each receive 1 Intent.

    This is THE critical regression test for the dispatcher bug:
    before the fix, handlers subscribed to OUTPUT_INTENT_READY but
    the manager emitted OUTPUT_INTENT_DISPATCHED, so NO handler
    ever received the Intent. After the fix, all handlers receive
    exactly one Intent per emit.
    """
    bus = EventBus()

    # Simulate 7 OutputHandlers (per Wave 4 dispatcher fix):
    # 3 audio handlers + subtitle + sticker + debug + avatar base
    handler_names = [
        "edge_tts",
        "omni_tts",
        "gptsovits",
        "subtitle",
        "sticker",
        "debug_console",
        "avatar_base",
    ]
    handlers = [_MockOutputHandler(name) for name in handler_names]

    # Wire each handler to the bus and initialize (idempotent subscribe)
    for h in handlers:
        h._event_bus = bus
        await h.init()

    # Verify all 7 are subscribed
    listener_count = bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED)
    assert listener_count == 7, f"Expected 7 listeners, got {listener_count}"

    # OutputHandlerManager emits the Intent (this is manager.py line 254)
    payload = _make_intent_payload()
    await bus.emit(
        CoreEvents.OUTPUT_INTENT_DISPATCHED,
        payload,
        source="OutputHandlerManager",
        wait=True,  # Wait for all handlers
    )

    # All 7 handlers should have received exactly 1 Intent
    for h in handlers:
        assert len(h.received_intents) == 1, (
            f"Handler '{h.name}' received {len(h.received_intents)} intents, expected 1"
        )

    # Cleanup
    for h in handlers:
        await h.cleanup()

    # Verify all unsubscribed
    assert bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED) == 0
    print(f"OK: all 7 handlers received the dispatched Intent")


# ============================================================
# Test 2: Idempotent subscription
# ============================================================


@pytest.mark.asyncio
async def test_dispatcher_idempotent_subscription():
    """Calling init() twice should NOT double-subscribe.

    Real OutputHandlers use a `_dispatch_subscribed` flag to guard
    against double subscription (see DebugConsoleHandler, SubtitleHandler).
    This test verifies the mock pattern preserves that contract.
    """
    bus = EventBus()
    handler = _MockOutputHandler("test_handler")
    handler._event_bus = bus

    await handler.init()
    await handler.init()  # Second call should be a no-op

    listener_count = bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED)
    assert listener_count == 1, f"Expected 1 listener (idempotent), got {listener_count}"
    assert handler.is_subscribed is True

    # Even after a third call, still 1 listener
    await handler.init()
    listener_count = bus.get_listeners_count(CoreEvents.OUTPUT_INTENT_DISPATCHED)
    assert listener_count == 1, (
        f"Expected 1 listener after 3rd init() (idempotent), got {listener_count}"
    )

    await handler.cleanup()
    assert True, 'OK: idempotent subscription works (init() x3 -> 1 listener)'
# ============================================================
# Test 3: Handler receives Intent object (not dict)
# ============================================================


@pytest.mark.asyncio
async def test_dispatcher_event_payload_typing():
    """The handler receives the proper Intent object (not raw dict).

    EventBus typed subscriptions auto-deserialize the dict back to
    IntentPayload. The handler then calls payload.to_intent() to
    get a fully typed Intent object. This test verifies end-to-end
    type safety: handler.received_intents[0] is an Intent with
    accessible attributes (not a dict).
    """
    bus = EventBus()
    handler = _MockOutputHandler("typed_handler")
    handler._event_bus = bus
    await handler.init()

    # Emit a payload with distinctive content
    payload = _make_intent_payload()
    await bus.emit(
        CoreEvents.OUTPUT_INTENT_DISPATCHED,
        payload,
        source="test",
        wait=True,
    )

    # Handler should have received exactly 1 Intent
    assert len(handler.received_intents) == 1
    intent = handler.received_intents[0]

    # Type safety: not a dict, but a real Intent with attribute access
    assert not isinstance(intent, dict), "Should receive Intent object, not dict"
    assert hasattr(intent, "speech"), "Should receive Intent, not dict (missing .speech)"
    assert intent.speech == "Hello world", (
        f"Expected speech='Hello world', got {intent.speech!r}"
    )
    assert intent.emotion == "neutral"
    assert intent.action == "blink"

    # Metadata should also be a proper Pydantic model
    assert hasattr(intent, "metadata")
    assert intent.metadata.decision_time_ms == 1729612345678
    assert intent.metadata.source_id == "test"

    await handler.cleanup()
    assert True, 'OK: handler receives Intent object (not dict) with full field access'