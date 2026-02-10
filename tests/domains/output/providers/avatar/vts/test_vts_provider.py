"""VTS Provider 测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.domains.output.providers.avatar.vts.vts_provider import VTSProvider
from src.core.types import EmotionType, ActionType, IntentAction
from src.domains.decision.intent import Intent


@pytest.fixture
def vts_config():
    return {"vts_host": "localhost", "vts_port": 8001, "lip_sync_enabled": True}


@pytest.fixture
def mock_vts_plugin():
    """创建一个完整的 mock VTS 插件"""
    vts_plugin = MagicMock()
    vts_plugin.connect = AsyncMock()
    vts_plugin.close = AsyncMock()
    vts_plugin.request = AsyncMock()
    vts_plugin.request_authenticate_token = AsyncMock()
    vts_plugin.request_authenticate = AsyncMock(return_value=True)
    return vts_plugin


class TestVTSProviderEmotionMap:
    def test_emotion_map_exists(self):
        assert hasattr(VTSProvider, "EMOTION_MAP")
        assert isinstance(VTSProvider.EMOTION_MAP, dict)

    def test_emotion_map_has_required_emotions(self):
        required = ["happy", "sad", "angry", "surprised", "shy", "love", "neutral"]
        for emotion in required:
            assert emotion in VTSProvider.EMOTION_MAP

    def test_emotion_map_happy(self):
        assert VTSProvider.EMOTION_MAP["happy"]["MouthSmile"] == 1.0


class TestVTSProviderAdaptIntent:
    def test_adapt_intent_with_happy_emotion(self, vts_config):
        provider = VTSProvider(vts_config)
        intent = Intent(original_text="你好", response_text="你好啊！", emotion=EmotionType.HAPPY, actions=[])
        result = provider._adapt_intent(intent)
        assert "MouthSmile" in result["expressions"]
        assert result["expressions"]["MouthSmile"] == 1.0

    def test_adapt_intent_with_neutral_emotion(self, vts_config):
        provider = VTSProvider(vts_config)
        intent = Intent(original_text="你好", response_text="你好", emotion=EmotionType.NEUTRAL, actions=[])
        result = provider._adapt_intent(intent)
        assert result["expressions"] == {}

    def test_adapt_intent_with_hotkey_action(self, vts_config):
        provider = VTSProvider(vts_config)
        action = IntentAction(type=ActionType.HOTKEY, params={"hotkey_id": "smile_01"})
        intent = Intent(original_text="测试", response_text="测试", emotion=EmotionType.NEUTRAL, actions=[action])
        result = provider._adapt_intent(intent)
        assert "smile_01" in result["hotkeys"]


class TestVTSProviderConfig:
    def test_init_with_default_config(self, vts_config):
        provider = VTSProvider(vts_config)
        assert provider.vts_host == "localhost"
        assert provider.vts_port == 8001

    def test_vts_parameter_constants(self, vts_config):
        provider = VTSProvider(vts_config)
        assert provider.PARAM_MOUTH_SMILE == "MouthSmile"
        assert provider.PARAM_EYE_OPEN_LEFT == "EyeOpenLeft"


class TestVTSProviderConnection:
    @pytest.mark.asyncio
    async def test_setup_internal(self, vts_config):
        provider = VTSProvider(vts_config)
        # Mock pyvts import by setting _vts directly
        provider._vts = MagicMock()
        provider._vts.connect = AsyncMock()
        await provider._setup_internal()
        # 如果没有 ImportError，说明 _vts 应该被设置
        # 但由于 pyvts 可能不存在，我们只验证不抛出其他异常

    @pytest.mark.asyncio
    async def test_connect_with_mock_vts(self, vts_config, mock_vts_plugin):
        provider = VTSProvider(vts_config)
        provider._vts = mock_vts_plugin
        mock_vts_plugin.request.return_value = {"messageType": "HotKeyListResponse", "data": {"availableHotkeys": []}}
        await provider._connect()
        assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect(self, vts_config, mock_vts_plugin):
        provider = VTSProvider(vts_config)
        provider._vts = mock_vts_plugin
        provider._is_connected = True
        await provider._disconnect()
        assert provider._is_connected is False
        mock_vts_plugin.close.assert_called_once()


class TestVTSProviderRendering:
    @pytest.mark.asyncio
    async def test_render_internal_with_expressions(self, vts_config, mock_vts_plugin):
        mock_vts_plugin.request.return_value = {"messageType": "InjectParameterDataResponse"}
        provider = VTSProvider(vts_config)
        provider._vts = mock_vts_plugin
        provider._is_connected = True

        params = {"expressions": {"MouthSmile": 0.8}, "hotkeys": []}
        await provider._render_internal(params)

        assert mock_vts_plugin.request.call_count >= 1
        assert provider.render_count == 1


class TestVTSProviderExpressions:
    @pytest.mark.asyncio
    async def test_smile_success(self, vts_config, mock_vts_plugin):
        mock_vts_plugin.request.return_value = {"messageType": "InjectParameterDataResponse"}
        provider = VTSProvider(vts_config)
        provider._vts = mock_vts_plugin
        provider._is_connected = True

        result = await provider.smile(0.8)
        assert result is True


class TestVTSProviderStats:
    def test_get_stats_initial(self, vts_config):
        provider = VTSProvider(vts_config)
        stats = provider.get_stats()
        assert stats["name"] == "VTSProvider"
        assert stats["is_connected"] is False
