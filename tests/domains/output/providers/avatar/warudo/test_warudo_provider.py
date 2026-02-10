"""Warudo Provider 测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.domains.output.providers.avatar.warudo.warudo_provider import WarudoOutputProvider
from src.core.types import EmotionType, ActionType, IntentAction
from src.domains.decision.intent import Intent


@pytest.fixture
def warudo_config():
    return {"ws_host": "localhost", "ws_port": 19190}


@pytest.fixture
def mock_websocket():
    """创建一个 mock WebSocket 连接"""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestWarudoProviderEmotionMap:
    def test_emotion_map_exists(self):
        assert hasattr(WarudoOutputProvider, "EMOTION_MAP")
        assert isinstance(WarudoOutputProvider.EMOTION_MAP, dict)

    def test_emotion_map_has_required_emotions(self):
        required = ["happy", "sad", "angry", "surprised", "shy", "love", "neutral"]
        for emotion in required:
            assert emotion in WarudoOutputProvider.EMOTION_MAP

    def test_emotion_map_happy(self):
        assert WarudoOutputProvider.EMOTION_MAP[EmotionType.HAPPY]["mouthSmile"] == 1.0


class TestWarudoProviderAdaptIntent:
    def test_adapt_intent_with_happy_emotion(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        intent = Intent(original_text="你好", response_text="你好啊！", emotion=EmotionType.HAPPY, actions=[])
        result = provider._adapt_intent(intent)
        assert "expressions" in result
        assert result["expressions"]["mouthSmile"] == 1.0

    def test_adapt_intent_with_neutral_emotion(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        intent = Intent(original_text="你好", response_text="你好", emotion=EmotionType.NEUTRAL, actions=[])
        result = provider._adapt_intent(intent)
        assert result["expressions"] == {}

    def test_adapt_intent_with_hotkey_action(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        action = IntentAction(type=ActionType.WAVE, params={})
        intent = Intent(original_text="测试", response_text="测试", emotion=EmotionType.NEUTRAL, actions=[action])
        result = provider._adapt_intent(intent)
        assert "wave" in result["hotkeys"]


class TestWarudoProviderConfig:
    def test_init_with_default_config(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        assert provider.ws_host == "localhost"
        assert provider.ws_port == 19190


class TestWarudoProviderConnection:
    @pytest.mark.asyncio
    async def test_connect_success(self, warudo_config, mock_websocket):
        provider = WarudoOutputProvider(warudo_config)
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            await provider._connect()
            assert provider._is_connected is True
            assert provider.websocket == mock_websocket

    @pytest.mark.asyncio
    async def test_disconnect(self, warudo_config, mock_websocket):
        provider = WarudoOutputProvider(warudo_config)
        provider.websocket = mock_websocket
        provider._is_connected = True
        await provider._disconnect()
        assert provider._is_connected is False
        assert provider.websocket is None
        mock_websocket.close.assert_called_once()


class TestWarudoProviderRendering:
    @pytest.mark.asyncio
    async def test_render_internal_with_expressions(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        # 创建 AsyncMock 的 websocket
        mock_ws = MagicMock()
        mock_ws.send_json = AsyncMock()
        provider.websocket = mock_ws
        provider._is_connected = True

        params = {"expressions": {"mouthSmile": 0.8}, "hotkeys": []}
        await provider._render_internal(params)

        assert mock_ws.send_json.call_count >= 1

    @pytest.mark.asyncio
    async def test_render_internal_when_not_connected(self, warudo_config):
        provider = WarudoOutputProvider(warudo_config)
        provider._is_connected = False

        params = {"expressions": {"mouthSmile": 0.8}, "hotkeys": []}
        # 应该不抛出异常，只是跳过渲染
        await provider._render_internal(params)
