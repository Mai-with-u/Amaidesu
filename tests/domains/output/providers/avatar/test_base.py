"""AvatarProviderBase 测试"""

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.modules.di.context import ProviderContext
from src.modules.types import Intent, SourceContext
from src.domains.output.providers.avatar.base import AvatarProviderBase
from src.modules.events.payloads.decision import IntentPayload
from src.modules.types import ActionType, EmotionType, IntentAction


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


@pytest.fixture
def mock_provider_context(mock_event_bus):
    """Mock ProviderContext for testing"""
    return ProviderContext(
        event_bus=mock_event_bus,
        config_service=MagicMock(),
    )


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
    def __init__(self, config: Dict[str, Any], context: ProviderContext):
        super().__init__(config, context)
        self.adapt_intent_calls = []
        self.render_to_platform_calls = []
        self.connect_calls = []
        self.disconnect_calls = []

    def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
        self.adapt_intent_calls.append(intent)
        return {"emotion": intent.emotion.value, "actions": len(intent.actions), "response": intent.response_text}

    async def _render_to_platform(self, params: Any) -> None:
        self.render_to_platform_calls.append(params)

    async def _connect(self) -> None:
        self.connect_calls.append(True)
        self._is_connected = True

    async def _disconnect(self) -> None:
        self.disconnect_calls.append(True)
        self._is_connected = False


class IncompleteAvatarProvider(AvatarProviderBase):
    def __init__(self, config: Dict[str, Any], context: ProviderContext):
        super().__init__(config, context)

    def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
        return {"test": "data"}

    async def _connect(self) -> None:
        self._is_connected = True

    async def _disconnect(self) -> None:
        self._is_connected = False


class TestAvatarProviderBaseAbstract:
    def test_complete_subclass_can_be_instantiated(self, mock_provider_context):
        config = {"test": "config"}
        provider = MockAvatarProvider(config, mock_provider_context)
        assert provider is not None
        assert provider.config == config
        assert provider._is_connected is False


class TestAvatarProviderBaseStart:
    @pytest.mark.asyncio
    async def test_start_subscribes_to_intent_event(self, mock_provider_context, mock_event_bus):
        from src.modules.events.names import CoreEvents

        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        mock_event_bus.on.assert_called_once()
        call_args = mock_event_bus.on.call_args
        assert call_args[0][0] == CoreEvents.OUTPUT_INTENT_READY

    @pytest.mark.asyncio
    async def test_start_connects_to_platform(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        assert len(provider.connect_calls) == 1
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_start_stores_event_bus_reference(self, mock_provider_context, mock_event_bus):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        assert provider.event_bus is mock_event_bus


class TestAvatarProviderBaseStop:
    @pytest.mark.asyncio
    async def test_stop_unsubscribes_from_intent_event(self, mock_provider_context, mock_event_bus):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        await provider.stop()
        mock_event_bus.off.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_disconnects_from_platform(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        await provider.stop()
        assert len(provider.disconnect_calls) == 1
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.stop()
        assert len(provider.disconnect_calls) == 0


class TestAvatarProviderBaseIntentProcessing:
    @pytest.mark.asyncio
    async def test_on_intent_calls_adapt_and_render(self, mock_provider_context, sample_intent_payload, sample_intent):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        await provider._on_intent("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 1
        assert len(provider.render_to_platform_calls) == 1
        assert provider.render_to_platform_calls[0]["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_on_intent_skips_when_not_connected(self, mock_provider_context, sample_intent_payload):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        provider._is_connected = False
        await provider._on_intent("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 0

    @pytest.mark.asyncio
    async def test_on_intent_handles_rendering_errors(self, mock_provider_context, sample_intent_payload):
        class ErrorAvatarProvider(MockAvatarProvider):
            async def _render_to_platform(self, params: Any) -> None:
                raise RuntimeError("渲染失败测试")

        provider = ErrorAvatarProvider({}, mock_provider_context)
        await provider.start()
        # Should catch and log error, not propagate
        await provider._on_intent("test_event", sample_intent_payload, "test_source")
        assert len(provider.adapt_intent_calls) == 1

    @pytest.mark.asyncio
    async def test_on_intent_handles_adaptation_errors(self, mock_provider_context, sample_intent_payload):
        class ErrorAdaptProvider(MockAvatarProvider):
            def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
                raise ValueError("适配失败测试")

        provider = ErrorAdaptProvider({}, mock_provider_context)
        await provider.start()
        await provider._on_intent("test_event", sample_intent_payload, "test_source")
        assert len(provider.render_to_platform_calls) == 0


class TestAvatarProviderBaseConnectionState:
    @pytest.mark.asyncio
    async def test_connection_state_initialized_false(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_connection_state_becomes_true_after_start(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_connection_state_becomes_false_after_stop(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        await provider.stop()
        assert provider._is_connected is False


class TestAvatarProviderBaseIntegration:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, mock_provider_context, sample_intent):
        provider = MockAvatarProvider({}, mock_provider_context)
        await provider.start()
        for _i in range(3):
            payload = IntentPayload.from_intent(sample_intent, provider="test")
            await provider._on_intent("test", payload, "test")
        assert len(provider.adapt_intent_calls) == 3
        await provider.stop()
        assert provider._is_connected is False


class TestAvatarProviderBaseHelpers:
    def test_logger_initialized(self, mock_provider_context):
        provider = MockAvatarProvider({}, mock_provider_context)
        assert provider.logger is not None

    def test_config_stored(self, mock_provider_context):
        config = {"test_key": "test_value"}
        provider = MockAvatarProvider(config, mock_provider_context)
        assert provider.config == config
