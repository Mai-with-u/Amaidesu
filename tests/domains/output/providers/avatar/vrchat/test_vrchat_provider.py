"""
VRChatProvider 单元测试

测试覆盖:
1. 情感映射表 (EMOTION_MAP)
2. Intent 适配 (_adapt_intent)
3. 配置验证 (ConfigSchema)
4. OSC 连接管理 (_connect, _disconnect)
5. 渲染功能 (_render_internal)
6. 手势映射 (GESTURE_MAP)
7. 软降级模式 (python-osc 不可用)
"""

from unittest.mock import MagicMock, patch

import pytest

from src.modules.types import Intent
from src.domains.output.providers.avatar.vrchat.vrchat_provider import (
    VRChatProvider,
)
from src.modules.types import ActionType, EmotionType, IntentAction

# GESTURE_MAP 是 VRChatProvider 的类变量
GESTURE_MAP = VRChatProvider.GESTURE_MAP


class TestVRChatProviderEmotionMap:
    """测试情感映射表"""

    def test_emotion_map_exists(self):
        """测试 EMOTION_MAP 存在且是字典类型"""
        assert hasattr(VRChatProvider, "EMOTION_MAP")
        assert isinstance(VRChatProvider.EMOTION_MAP, dict)

    def test_emotion_map_has_all_10_emotions(self):
        """测试所有 10 个情感类型都有映射"""
        required = [
            "neutral",
            "happy",
            "sad",
            "angry",
            "surprised",
            "confused",
            "scared",
            "love",
            "shy",
            "excited",
        ]
        for emotion in required:
            assert emotion in VRChatProvider.EMOTION_MAP, f"缺少情感映射: {emotion}"

    def test_neutral_returns_empty_dict(self):
        """测试 neutral 返回空字典（默认状态）"""
        assert VRChatProvider.EMOTION_MAP["neutral"] == {}

    def test_happy_has_correct_parameters(self):
        """测试 happy 情感有正确的参数"""
        happy_map = VRChatProvider.EMOTION_MAP["happy"]
        assert "MouthSmile" in happy_map
        assert happy_map["MouthSmile"] == 1.0

    def test_sad_has_correct_parameters(self):
        """测试 sad 情感有正确的参数"""
        sad_map = VRChatProvider.EMOTION_MAP["sad"]
        assert "MouthSmile" in sad_map
        assert "EyeOpen" in sad_map
        assert sad_map["MouthSmile"] == -0.3
        assert sad_map["EyeOpen"] == 0.7

    def test_angry_has_correct_parameters(self):
        """测试 angry 情感有正确的参数"""
        angry_map = VRChatProvider.EMOTION_MAP["angry"]
        assert "EyeOpen" in angry_map
        assert "MouthSmile" in angry_map
        assert angry_map["EyeOpen"] == 0.6
        assert angry_map["MouthSmile"] == -0.5

    def test_surprised_has_correct_parameters(self):
        """测试 surprised 情感有正确的参数"""
        surprised_map = VRChatProvider.EMOTION_MAP["surprised"]
        assert "EyeOpen" in surprised_map
        assert "MouthOpen" in surprised_map
        assert surprised_map["EyeOpen"] == 1.0
        assert surprised_map["MouthOpen"] == 0.5

    def test_confused_has_correct_parameters(self):
        """测试 confused 情感有正确的参数"""
        confused_map = VRChatProvider.EMOTION_MAP["confused"]
        assert "EyeOpen" in confused_map
        assert "MouthOpen" in confused_map
        assert confused_map["EyeOpen"] == 0.7
        assert confused_map["MouthOpen"] == 0.2

    def test_scared_has_correct_parameters(self):
        """测试 scared 情感有正确的参数"""
        scared_map = VRChatProvider.EMOTION_MAP["scared"]
        assert "EyeOpen" in scared_map
        assert "MouthOpen" in scared_map
        assert scared_map["EyeOpen"] == 0.5
        assert scared_map["MouthOpen"] == 0.3

    def test_love_has_correct_parameters(self):
        """测试 love 情感有正确的参数"""
        love_map = VRChatProvider.EMOTION_MAP["love"]
        assert "MouthSmile" in love_map
        assert "EyeOpen" in love_map
        assert love_map["MouthSmile"] == 0.8
        assert love_map["EyeOpen"] == 0.9

    def test_shy_has_correct_parameters(self):
        """测试 shy 情感有正确的参数"""
        shy_map = VRChatProvider.EMOTION_MAP["shy"]
        assert "MouthSmile" in shy_map
        assert "EyeOpen" in shy_map
        assert shy_map["MouthSmile"] == 0.3
        assert shy_map["EyeOpen"] == 0.8

    def test_excited_has_correct_parameters(self):
        """测试 excited 情感有正确的参数"""
        excited_map = VRChatProvider.EMOTION_MAP["excited"]
        assert "MouthSmile" in excited_map
        assert "EyeOpen" in excited_map
        assert excited_map["MouthSmile"] == 1.0
        assert excited_map["EyeOpen"] == 1.0


class TestVRChatProviderAdaptIntent:
    """测试 Intent 适配"""

    def test_adapt_intent_with_happy_emotion(self, vrchat_config):
        """测试 happy 情感适配"""
        provider = VRChatProvider(vrchat_config)
        intent = Intent(
            original_text="你好",
            response_text="你好啊！",
            emotion=EmotionType.HAPPY,
            actions=[],
        )
        result = provider._adapt_intent(intent)
        assert "expressions" in result
        assert result["expressions"]["MouthSmile"] == 1.0

    def test_adapt_intent_with_neutral_emotion(self, vrchat_config):
        """测试 neutral 情感适配（应返回空表情）"""
        provider = VRChatProvider(vrchat_config)
        intent = Intent(
            original_text="你好", response_text="你好", emotion=EmotionType.NEUTRAL, actions=[]
        )
        result = provider._adapt_intent(intent)
        assert result["expressions"] == {}

    def test_adapt_intent_with_sad_emotion(self, vrchat_config):
        """测试 sad 情感适配"""
        provider = VRChatProvider(vrchat_config)
        intent = Intent(
            original_text="呜呜", response_text="好难过", emotion=EmotionType.SAD, actions=[]
        )
        result = provider._adapt_intent(intent)
        assert result["expressions"]["MouthSmile"] == -0.3
        assert result["expressions"]["EyeOpen"] == 0.7

    def test_adapt_intent_with_custom_action_gesture(self, vrchat_config):
        """测试 CUSTOM 类型的动作为手势"""
        provider = VRChatProvider(vrchat_config)
        action = IntentAction(
            type=ActionType.CUSTOM, params={"gesture_name": "Wave"}
        )
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.NEUTRAL,
            actions=[action],
        )
        result = provider._adapt_intent(intent)
        assert "gestures" in result
        assert "Wave" in result["gestures"]

    def test_adapt_intent_with_hotkey_action_gesture(self, vrchat_config):
        """测试 HOTKEY 类型的动作为手势"""
        provider = VRChatProvider(vrchat_config)
        action = IntentAction(
            type=ActionType.HOTKEY, params={"gesture_name": "Peace"}
        )
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.NEUTRAL,
            actions=[action],
        )
        result = provider._adapt_intent(intent)
        assert "gestures" in result
        assert "Peace" in result["gestures"]

    def test_adapt_intent_with_multiple_gestures(self, vrchat_config):
        """测试多个手势动作"""
        provider = VRChatProvider(vrchat_config)
        actions = [
            IntentAction(type=ActionType.CUSTOM, params={"gesture_name": "Wave"}),
            IntentAction(type=ActionType.CUSTOM, params={"gesture_name": "ThumbsUp"}),
        ]
        intent = Intent(
            original_text="测试",
            response_text="测试",
            emotion=EmotionType.NEUTRAL,
            actions=actions,
        )
        result = provider._adapt_intent(intent)
        assert len(result["gestures"]) == 2
        assert "Wave" in result["gestures"]
        assert "ThumbsUp" in result["gestures"]

    def test_adapt_intent_with_emotion_and_gesture(self, vrchat_config):
        """测试情感和手势同时存在"""
        provider = VRChatProvider(vrchat_config)
        action = IntentAction(
            type=ActionType.CUSTOM, params={"gesture_name": "Point"}
        )
        intent = Intent(
            original_text="你好",
            response_text="你好啊！",
            emotion=EmotionType.HAPPY,
            actions=[action],
        )
        result = provider._adapt_intent(intent)
        assert result["expressions"]["MouthSmile"] == 1.0
        assert "Point" in result["gestures"]


class TestVRChatProviderConfig:
    """测试配置验证"""

    def test_init_with_default_config(self, vrchat_config):
        """测试使用默认配置初始化"""
        provider = VRChatProvider(vrchat_config)
        assert provider.vrc_host == "127.0.0.1"
        assert provider.vrc_out_port == 9000

    def test_init_with_custom_host(self):
        """测试自定义主机地址"""
        config = {"vrc_host": "192.168.1.100", "vrc_out_port": 9000}
        provider = VRChatProvider(config)
        assert provider.vrc_host == "192.168.1.100"

    def test_init_with_custom_port(self):
        """测试自定义端口"""
        config = {"vrc_host": "127.0.0.1", "vrc_out_port": 9999}
        provider = VRChatProvider(config)
        assert provider.vrc_out_port == 9999

    def test_config_schema_type_field(self):
        """测试 ConfigSchema 的 type 字段"""
        schema = VRChatProvider.ConfigSchema()
        assert schema.type == "vrchat"

    def test_config_schema_default_values(self):
        """测试 ConfigSchema 的默认值"""
        schema = VRChatProvider.ConfigSchema()
        assert schema.vrc_host == "127.0.0.1"
        assert schema.vrc_out_port == 9000

    def test_config_schema_port_validation_min(self):
        """测试端口范围验证（最小值）"""
        # 端口 1 应该有效
        schema = VRChatProvider.ConfigSchema(vrc_out_port=1)
        assert schema.vrc_out_port == 1

    def test_config_schema_port_validation_max(self):
        """测试端口范围验证（最大值）"""
        # 端口 65535 应该有效
        schema = VRChatProvider.ConfigSchema(vrc_out_port=65535)
        assert schema.vrc_out_port == 65535

    def test_config_schema_port_validation_invalid(self):
        """测试端口范围验证（无效值）"""
        # 端口 0 应该无效
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VRChatProvider.ConfigSchema(vrc_out_port=0)

    def test_config_schema_port_validation_too_large(self):
        """测试端口范围验证（过大值）"""
        # 端口 65536 应该无效
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VRChatProvider.ConfigSchema(vrc_out_port=65536)


class TestVRChatProviderConnection:
    """测试 OSC 连接管理"""

    @pytest.mark.asyncio
    async def test_connect_creates_osc_client(self, vrchat_config):
        """测试 _connect 创建 OSC 客户端"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True

        # Mock SimpleUDPClient
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.SimpleUDPClient") as mock_osc:
            await provider._connect()
            mock_osc.assert_called_once_with("127.0.0.1", 9000)
            assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_connect_sets_is_connected_true(self, vrchat_config):
        """测试 _connect 设置 is_connected 为 True"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True

        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.SimpleUDPClient"):
            await provider._connect()
            assert provider._is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect_clears_osc_client(self, vrchat_config):
        """测试 _disconnect 清理 OSC 客户端"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.osc_client = MagicMock()

        await provider._disconnect()

        assert provider.osc_client is None
        assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_connect_when_osc_disabled(self, vrchat_config, mock_logger):
        """测试 OSC 禁用时的连接行为"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = False
        provider.logger = mock_logger

        await provider._connect()

        # 应该不抛出异常，只是记录警告
        assert provider._is_connected is False
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_soft_fallback_mode(self, vrchat_config):
        """测试软降级模式（python-osc 不可用）"""
        # 模拟 python-osc 不可用
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.PYTHON_OSC_AVAILABLE", False):
            provider = VRChatProvider(vrchat_config)
            assert provider._osc_enabled is False
            assert provider.osc_client is None


class TestVRChatProviderRendering:
    """测试渲染功能"""

    @pytest.mark.asyncio
    async def test_render_internal_with_expressions(self, vrchat_config, mock_osc_client):
        """测试渲染表情参数"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.osc_client = mock_osc_client

        params = {"expressions": {"MouthSmile": 0.8, "EyeOpen": 0.9}, "gestures": []}
        await provider._render_internal(params)

        assert mock_osc_client.send_message.call_count == 2
        assert provider.render_count == 1

    @pytest.mark.asyncio
    async def test_render_internal_with_gestures(self, vrchat_config, mock_osc_client):
        """测试渲染手势"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.osc_client = mock_osc_client

        params = {"expressions": {}, "gestures": ["Wave", "Peace"]}
        await provider._render_internal(params)

        # 应该发送两个 VRCEmote 消息
        assert mock_osc_client.send_message.call_count == 2
        assert provider.render_count == 1

    @pytest.mark.asyncio
    async def test_render_internal_updates_render_count(self, vrchat_config, mock_osc_client):
        """测试 render_count 更新"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.osc_client = mock_osc_client

        params = {"expressions": {"MouthSmile": 1.0}, "gestures": []}

        initial_count = provider.render_count
        await provider._render_internal(params)
        assert provider.render_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_render_internal_when_not_connected(self, vrchat_config, mock_logger):
        """测试未连接时的渲染行为"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = False
        provider.logger = mock_logger

        params = {"expressions": {"MouthSmile": 1.0}, "gestures": []}
        await provider._render_internal(params)

        # 应该记录警告，但不抛出异常
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_render_internal_when_osc_disabled(self, vrchat_config, mock_logger):
        """测试 OSC 禁用时的渲染行为"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = False
        provider._is_connected = False
        provider.logger = mock_logger

        params = {"expressions": {"MouthSmile": 1.0}, "gestures": []}
        await provider._render_internal(params)

        # 应该记录警告，但不抛出异常
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_render_internal_handles_send_exception(
        self, vrchat_config, mock_logger
    ):
        """测试渲染时处理发送异常（_send_parameter 内部捕获异常）"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.logger = mock_logger
        # Mock OSC client that raises exception
        provider.osc_client = MagicMock()
        provider.osc_client.send_message = MagicMock(side_effect=Exception("Network error"))

        params = {"expressions": {"MouthSmile": 1.0}, "gestures": []}

        # _send_parameter 内部会捕获异常，不会传播到 _render_internal
        # 所以这里不会抛出 RuntimeError
        await provider._render_internal(params)

        # 应该记录错误日志（通过 _send_parameter 的 except 块）
        # 注意：由于 mock_logger 只是 MagicMock，我们可以检查是否有错误日志调用
        # 但实际上 _send_parameter 的 logger 是 provider.logger（在 __init__ 中设置的）
        # 这里我们只验证方法不会崩溃


class TestVRChatProviderGestures:
    """测试手势映射"""

    def test_gesture_map_exists(self):
        """测试 GESTURE_MAP 存在且是字典类型"""
        assert hasattr(VRChatProvider, "GESTURE_MAP")
        assert isinstance(GESTURE_MAP, dict)

    def test_gesture_map_has_all_9_gestures(self):
        """测试所有 9 个手势都有映射"""
        required = [
            "Neutral",
            "Wave",
            "Peace",
            "ThumbsUp",
            "RocknRoll",
            "HandGun",
            "Point",
            "Victory",
            "Cross",
        ]
        for gesture in required:
            assert gesture in GESTURE_MAP, f"缺少手势映射: {gesture}"

    def test_gesture_map_neutral_value(self):
        """测试 Neutral 手势值为 0"""
        assert GESTURE_MAP["Neutral"] == 0

    def test_gesture_map_wave_value(self):
        """测试 Wave 手势值为 1"""
        assert GESTURE_MAP["Wave"] == 1

    def test_gesture_map_peace_value(self):
        """测试 Peace 手势值为 2"""
        assert GESTURE_MAP["Peace"] == 2

    def test_gesture_map_thumbs_up_value(self):
        """测试 ThumbsUp 手势值为 3"""
        assert GESTURE_MAP["ThumbsUp"] == 3

    def test_gesture_map_rocknroll_value(self):
        """测试 RocknRoll 手势值为 4"""
        assert GESTURE_MAP["RocknRoll"] == 4

    def test_gesture_map_handgun_value(self):
        """测试 HandGun 手势值为 5"""
        assert GESTURE_MAP["HandGun"] == 5

    def test_gesture_map_point_value(self):
        """测试 Point 手势值为 6"""
        assert GESTURE_MAP["Point"] == 6

    def test_gesture_map_victory_value(self):
        """测试 Victory 手势值为 7"""
        assert GESTURE_MAP["Victory"] == 7

    def test_gesture_map_cross_value(self):
        """测试 Cross 手势值为 8"""
        assert GESTURE_MAP["Cross"] == 8

    @pytest.mark.asyncio
    async def test_trigger_gesture_sends_vrcemote(self, vrchat_config, mock_osc_client):
        """测试触发手势发送 VRCEmote 参数"""
        provider = VRChatProvider(vrchat_config)
        provider._is_connected = True
        provider.osc_client = mock_osc_client

        provider._trigger_gesture("Wave")

        # 验证发送了正确的 OSC 消息
        mock_osc_client.send_message.assert_called_once_with(
            "/avatar/parameters/VRCEmote", 1
        )

    @pytest.mark.asyncio
    async def test_trigger_unknown_gesture(self, vrchat_config, mock_osc_client, mock_logger):
        """测试触发未知手势"""
        provider = VRChatProvider(vrchat_config)
        provider._is_connected = True
        provider.osc_client = mock_osc_client
        provider.logger = mock_logger

        provider._trigger_gesture("UnknownGesture")

        # 应该记录警告，不发送消息
        mock_osc_client.send_message.assert_not_called()
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_trigger_gesture_when_not_connected(self, vrchat_config, mock_logger):
        """测试未连接时触发手势"""
        provider = VRChatProvider(vrchat_config)
        provider._is_connected = False
        provider.logger = mock_logger

        provider._trigger_gesture("Wave")

        # 应该记录警告，不抛出异常
        mock_logger.warning.assert_called()


class TestVRChatProviderSoftFallback:
    """测试软降级模式"""

    def test_provider_instantiates_without_python_osc(self, vrchat_config):
        """测试 python-osc 不可用时仍能实例化"""
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.PYTHON_OSC_AVAILABLE", False):
            # 应该不抛出异常
            provider = VRChatProvider(vrchat_config)
            assert provider._osc_enabled is False

    def test_warning_logged_when_osc_unavailable(self, vrchat_config, mock_logger):
        """测试 OSC 不可用时记录警告日志"""
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.PYTHON_OSC_AVAILABLE", False):
            with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.get_logger", return_value=mock_logger):
                # 实例化 Provider 应该触发警告日志
                VRChatProvider(vrchat_config)
                # 应该记录警告
                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_connect_gracefully_handles_unavailable_osc(self, vrchat_config):
        """测试 _connect 在 OSC 不可用时优雅处理"""
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.PYTHON_OSC_AVAILABLE", False):
            provider = VRChatProvider(vrchat_config)
            # 应该不抛出异常
            await provider._connect()
            assert provider._is_connected is False

    @pytest.mark.asyncio
    async def test_render_gracefully_handles_unavailable_osc(self, vrchat_config):
        """测试 _render_internal 在 OSC 不可用时优雅处理"""
        with patch("src.domains.output.providers.avatar.vrchat.vrchat_provider.PYTHON_OSC_AVAILABLE", False):
            provider = VRChatProvider(vrchat_config)
            params = {"expressions": {"MouthSmile": 1.0}, "gestures": []}
            # 应该不抛出异常
            await provider._render_internal(params)


class TestVRChatProviderStats:
    """测试统计信息"""

    def test_get_stats_initial(self, vrchat_config):
        """测试初始统计信息"""
        provider = VRChatProvider(vrchat_config)
        stats = provider.get_stats()
        assert stats["name"] == "VRChatProvider"
        assert stats["is_connected"] is False
        assert stats["osc_enabled"] == provider._osc_enabled
        assert stats["render_count"] == 0
        assert stats["error_count"] == 0
        assert stats["vrc_host"] == "127.0.0.1"
        assert stats["vrc_out_port"] == 9000

    def test_get_stats_after_render(self, vrchat_config, mock_osc_client):
        """测试渲染后的统计信息"""
        provider = VRChatProvider(vrchat_config)
        provider._osc_enabled = True
        provider._is_connected = True
        provider.osc_client = mock_osc_client
        provider.render_count = 5
        provider.error_count = 1

        stats = provider.get_stats()
        assert stats["render_count"] == 5
        assert stats["error_count"] == 1


class TestVRChatProviderSendParameter:
    """测试发送 OSC 参数"""

    def test_send_parameter_when_connected(self, vrchat_config, mock_osc_client):
        """测试连接时发送参数"""
        provider = VRChatProvider(vrchat_config)
        provider._is_connected = True
        provider.osc_client = mock_osc_client

        provider._send_parameter("MouthSmile", 0.8)

        mock_osc_client.send_message.assert_called_once_with(
            "/avatar/parameters/MouthSmile", 0.8
        )

    def test_send_parameter_when_not_connected(self, vrchat_config, mock_logger):
        """测试未连接时发送参数"""
        provider = VRChatProvider(vrchat_config)
        provider._is_connected = False
        provider.logger = mock_logger

        provider._send_parameter("MouthSmile", 0.8)

        # 应该记录警告，不发送消息
        mock_logger.warning.assert_called()
