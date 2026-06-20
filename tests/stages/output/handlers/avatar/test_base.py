"""AvatarHandlerBase 测试 - 新架构版本"""

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.modules.types import Intent, IntentMetadata
from src.stages.output.handlers.avatar.base import AvatarHandlerBase
from src.modules.events.event_bus import EventBus


@pytest.fixture
def mock_event_bus():
    event_bus = MagicMock(spec=EventBus)
    event_bus.on = MagicMock()
    event_bus.off = MagicMock()
    return event_bus


@pytest.fixture
def sample_intent():
    return Intent(
        emotion="happy",
        action="smile",
        speech="你好！很高兴见到你~",
        context=None,
        metadata=IntentMetadata(
            source_id="test_source",
            decision_time=1234567890123,
        ),
    )


class MockAvatarProvider(AvatarHandlerBase):
    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        super().__init__(config, event_bus)
        self.adapt_intent_calls = []
        self.render_to_platform_calls = []
        self.connect_calls = []
        self.disconnect_calls = []

    async def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
        self.adapt_intent_calls.append(intent)
        return {"emotion": intent.emotion, "action": intent.action, "response": intent.speech}

    async def _render_to_platform(self, params: Any) -> None:
        self.render_to_platform_calls.append(params)

    async def _connect(self) -> None:
        self.connect_calls.append(True)
        self._is_connected = True

    async def _disconnect(self) -> None:
        self.disconnect_calls.append(True)
        self._is_connected = False


class TestAvatarHandlerBaseAbstract:
    def test_complete_subclass_can_be_instantiated(self, mock_event_bus):
        config = {"test": "config"}
        provider = MockAvatarProvider(config, mock_event_bus)
        assert provider is not None
        assert provider.config == config
        assert provider._is_connected is False


class TestAvatarHandlerBaseLifecycle:
    @pytest.mark.asyncio
    async def test_start_connects_to_platform(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.start()
        assert len(provider.connect_calls) == 1
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_stop_disconnects_from_platform(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.start()
        await provider.stop()
        assert len(provider.disconnect_calls) == 1
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.stop()
        assert len(provider.disconnect_calls) == 0


class TestAvatarHandlerBaseHandle:
    @pytest.mark.asyncio
    async def test_handle_calls_adapt_and_render(self, mock_event_bus, sample_intent):
        provider = MockAvatarProvider({}, mock_event_bus)
        provider._is_connected = True
        await provider.handle(sample_intent)
        assert len(provider.adapt_intent_calls) == 1
        assert len(provider.render_to_platform_calls) == 1
        assert provider.render_to_platform_calls[0]["emotion"] == "happy"

    @pytest.mark.asyncio
    async def test_handle_skips_when_not_connected(self, mock_event_bus, sample_intent):
        provider = MockAvatarProvider({}, mock_event_bus)
        provider._is_connected = False
        await provider.handle(sample_intent)
        assert len(provider.adapt_intent_calls) == 0

    @pytest.mark.asyncio
    async def test_handle_handles_rendering_errors(self, mock_event_bus, sample_intent):
        class ErrorAvatarProvider(MockAvatarProvider):
            async def _render_to_platform(self, params: Any) -> None:
                raise RuntimeError("渲染失败测试")

        provider = ErrorAvatarProvider({}, mock_event_bus)
        provider._is_connected = True
        # Should catch and log error, not propagate
        await provider.handle(sample_intent)
        assert len(provider.adapt_intent_calls) == 1

    @pytest.mark.asyncio
    async def test_handle_handles_adaptation_errors(self, mock_event_bus, sample_intent):
        class ErrorAdaptProvider(MockAvatarProvider):
            async def _adapt_intent(self, intent: Intent) -> Dict[str, Any]:
                raise ValueError("适配失败测试")

        provider = ErrorAdaptProvider({}, mock_event_bus)
        provider._is_connected = True
        await provider.handle(sample_intent)
        assert len(provider.render_to_platform_calls) == 0


class TestAvatarHandlerBaseConnectionState:
    @pytest.mark.asyncio
    async def test_connection_state_initialized_false(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_connection_state_becomes_true_after_start(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.start()
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_connection_state_becomes_false_after_stop(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.start()
        await provider.stop()
        assert provider._is_connected is False


class TestAvatarHandlerBaseIntegration:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, mock_event_bus, sample_intent):
        provider = MockAvatarProvider({}, mock_event_bus)
        await provider.start()
        for _i in range(3):
            await provider.handle(sample_intent)
        assert len(provider.adapt_intent_calls) == 3
        await provider.stop()
        assert provider._is_connected is False


class TestAvatarHandlerBaseHelpers:
    def test_logger_initialized(self, mock_event_bus):
        provider = MockAvatarProvider({}, mock_event_bus)
        assert provider.logger is not None

    def test_config_stored(self, mock_event_bus):
        config = {"test_key": "test_value"}
        provider = MockAvatarProvider(config, mock_event_bus)
        assert provider.config == config