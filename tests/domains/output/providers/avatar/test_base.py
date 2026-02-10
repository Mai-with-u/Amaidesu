"""AvatarProviderBase 测试"""

import pytest
from typing import Any, Dict
from unittest.mock import MagicMock

from src.domains.output.providers.avatar.base import AvatarProviderBase
from src.domains.decision.intent import Intent, SourceContext
from src.core.events.payloads.decision import IntentPayload
from src.core.types import EmotionType, ActionType, IntentAction


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


@pytest.fixture
def sample_intent():
    return Intent(
        original_text="你好",
        response_text="你好！很高兴见到你~",
        emotion=EmotionType.HAPPY,
        actions=[
            IntentAction(type=ActionType.EXPRESSION, params={"smile": 0.8}),
            IntentAction(type=ActionType.HOTKEY, params={"hotkey": "wave"}),
        ],
        source_context=SourceContext(
            source="console_input",
            data_type="text",
            user_id="test_user",
            user_nickname="测试用户",
        ),
    )


@pytest.fixture
def sample_intent_payload(sample_intent):
    return IntentPayload.from_intent(sample_intent, provider="test_provider")


class MockAvatarProvider(AvatarProviderBase):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.adapt_intent_calls = []
        self.render_internal_calls = []
        self.connect_calls = []
        self.disconnect_calls = []

    def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
        self.adapt_intent_calls.append(intent)
        return {"emotion": intent.emotion.value, "actions": len(intent.actions), "response": intent.response_text}

    async def _render_internal(self, params: Any) -> None:
        self.render_internal_calls.append(params)

    async def _connect(self) -> None:
        self.connect_calls.append(True)
        self._is_connected = True

    async def _disconnect(self) -> None:
        self.disconnect_calls.append(True)
        self._is_connected = False


class IncompleteAvatarProvider(AvatarProviderBase):
    def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
        return {"test": "data"}

    async def _connect(self) -> None:
        self._is_connected = True

    async def _disconnect(self) -> None:
        self._is_connected = False


class TestAvatarProviderBaseAbstract:
    def test_cannot_instantiate_abstract_base_class(self):
        config = {}
        with pytest.raises(TypeError) as exc_info:
            AvatarProviderBase(config)
        assert "abstract" in str(exc_info.value).lower()

    def test_subclass_must_implement_all_abstract_methods(self):
        config = {}
        with pytest.raises(TypeError) as exc_info:
            IncompleteAvatarProvider(config)
        assert "abstract" in str(exc_info.value).lower()

    def test_complete_subclass_can_be_instantiated(self):
        config = {"test": "config"}
        provider = MockAvatarProvider(config)
        assert provider is not None
        assert provider.config == config
        assert provider._is_connected is False


class TestAvatarProviderBaseSetup:
    @pytest.mark.asyncio
    async def test_setup_subscribes_to_intent_event(self, mock_event_bus):
        from src.core.events.names import CoreEvents

        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        mock_event_bus.on.assert_called_once()
        call_args = mock_event_bus.on.call_args
        assert call_args[0][0] == CoreEvents.DECISION_INTENT_GENERATED

    @pytest.mark.asyncio
    async def test_setup_connects_to_platform(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        assert len(provider.connect_calls) == 1
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_setup_stores_event_bus_reference(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        assert provider.event_bus is mock_event_bus


class TestAvatarProviderBaseCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_unsubscribes_from_intent_event(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider.cleanup()
        mock_event_bus.off.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_disconnects_from_platform(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider.cleanup()
        assert len(provider.disconnect_calls) == 1
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_cleanup_without_setup(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.cleanup()
        assert len(provider.disconnect_calls) == 1


class TestAvatarProviderBaseIntentProcessing:
    @pytest.mark.asyncio
    async def test_on_intent_ready_calls_adapt_and_render(self, mock_event_bus, sample_intent_payload, sample_intent):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider._on_intent_ready("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 1
        assert len(provider.render_internal_calls) == 1
        assert provider.render_internal_calls[0]["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_on_intent_ready_skips_when_not_connected(self, mock_event_bus, sample_intent_payload):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        provider._is_connected = False
        await provider._on_intent_ready("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 0

    @pytest.mark.asyncio
    async def test_on_intent_ready_handles_invalid_payload(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider._on_intent_ready("test_event", {"invalid": "payload"}, "test_source")
        assert len(provider.adapt_intent_calls) == 0

    @pytest.mark.asyncio
    async def test_on_intent_ready_handles_rendering_errors(self, mock_event_bus, sample_intent_payload):
        class ErrorAvatarProvider(MockAvatarProvider):
            async def _render_internal(self, params: Any) -> None:
                raise RuntimeError("渲染失败测试")

        provider = ErrorAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider._on_intent_ready("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 1

    @pytest.mark.asyncio
    async def test_on_intent_ready_handles_adaptation_errors(self, mock_event_bus, sample_intent_payload):
        class ErrorAdaptProvider(MockAvatarProvider):
            def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
                raise ValueError("适配失败测试")

        provider = ErrorAdaptProvider({})
        await provider.setup(mock_event_bus)
        await provider._on_intent_ready("test_event", sample_intent_payload, "test_source")
        assert len(provider.render_internal_calls) == 0


class TestAvatarProviderBaseConnectionState:
    @pytest.mark.asyncio
    async def test_connection_state_initialized_false(self):
        provider = MockAvatarProvider({})
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_connection_state_becomes_true_after_setup(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_connection_state_becomes_false_after_cleanup(self, mock_event_bus):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        await provider.cleanup()
        assert provider._is_connected is False


class TestAvatarProviderBaseIntegration:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, mock_event_bus, sample_intent):
        provider = MockAvatarProvider({})
        await provider.setup(mock_event_bus)
        for _i in range(3):
            payload = IntentPayload.from_intent(sample_intent, provider="test")
            await provider._on_intent_ready("test", payload, "test")
        assert len(provider.adapt_intent_calls) == 3
        await provider.cleanup()
        assert provider._is_connected is False


class TestAvatarProviderBaseHelpers:
    def test_logger_initialized(self):
        provider = MockAvatarProvider({})
        assert provider.logger is not None

    def test_config_stored(self):
        config = {"test_key": "test_value"}
        provider = MockAvatarProvider(config)
        assert provider.config == config
