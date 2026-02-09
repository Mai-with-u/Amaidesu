"""
VRChatAdapter 测试
"""

import pytest
from unittest.mock import MagicMock, patch
from src.domains.output.adapters.vrchat.vrchat_adapter import (
    VRChatAdapter,
    VRChatAdapterConfig,
)


class TestVRChatAdapterConfig:
    """测试 VRChatAdapter 配置"""

    def test_default_config(self):
        """测试默认配置"""
        config = VRChatAdapterConfig()

        assert config.vrc_host == "127.0.0.1"
        assert config.vrc_out_port == 9000
        assert config.vrc_in_port == 9001
        assert config.enable_server is False
        assert config.auto_send_params is True
        assert config.local_host == "127.0.0.1"

    def test_custom_config(self):
        """测试自定义配置"""
        config = VRChatAdapterConfig(
            vrc_host="192.168.1.100",
            vrc_out_port=9002,
            vrc_in_port=9003,
            enable_server=True,
            auto_send_params=False,
        )

        assert config.vrc_host == "192.168.1.100"
        assert config.vrc_out_port == 9002
        assert config.vrc_in_port == 9003
        assert config.enable_server is True
        assert config.auto_send_params is False


@pytest.fixture
def vrchat_config():
    """VRChat 适配器配置"""
    return {
        "vrc_host": "127.0.0.1",
        "vrc_out_port": 9000,
        "vrc_in_port": 9001,
        "enable_server": False,
        "auto_send_params": True,
    }


@pytest.fixture
def mock_osc_client():
    """Mock OSC 客户端"""
    with patch("src.domains.output.adapters.vrchat.vrchat_adapter.SimpleUDPClient") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


class TestVRChatAdapter:
    """测试 VRChatAdapter"""

    def test_init(self, vrchat_config):
        """测试初始化"""
        adapter = VRChatAdapter(vrchat_config)

        assert adapter.adapter_name == "vrchat"
        assert adapter.is_connected is False
        assert adapter.typed_config.vrc_host == "127.0.0.1"
        assert adapter.typed_config.vrc_in_port == 9001

    def test_param_translation(self, vrchat_config):
        """测试参数翻译"""
        adapter = VRChatAdapter(vrchat_config)

        # 测试已映射的参数
        abstract_params = {
            "smile": 0.8,
            "eye_open": 0.5,
            "mouth_open": 0.3,
            "brow_down": 0.2,
        }

        vrc_params = adapter.translate_params(abstract_params)

        assert vrc_params["MouthSmile"] == 0.8
        assert vrc_params["EyeOpen"] == 0.5
        assert vrc_params["MouthOpen"] == 0.3
        assert vrc_params["BrowDownLeft"] == 0.2

    def test_param_translation_unmapped(self, vrchat_config):
        """测试未映射参数（直接使用原名）"""
        adapter = VRChatAdapter(vrchat_config)

        abstract_params = {"custom_param": 0.5, "another_param": 0.8}

        vrc_params = adapter.translate_params(abstract_params)

        # 未映射的参数应该直接使用原名
        assert vrc_params["custom_param"] == 0.5
        assert vrc_params["another_param"] == 0.8

    def test_gesture_map(self, vrchat_config):
        """测试手势映射"""
        adapter = VRChatAdapter(vrchat_config)

        assert adapter.GESTURE_MAP["Neutral"] == 0
        assert adapter.GESTURE_MAP["Wave"] == 1
        assert adapter.GESTURE_MAP["Peace"] == 2
        assert adapter.GESTURE_MAP["ThumbsUp"] == 3
        assert adapter.GESTURE_MAP["RocknRoll"] == 4
        assert adapter.GESTURE_MAP["HandGun"] == 5
        assert adapter.GESTURE_MAP["Point"] == 6
        assert adapter.GESTURE_MAP["Victory"] == 7
        assert adapter.GESTURE_MAP["Cross"] == 8

    @pytest.mark.asyncio
    async def test_connect(self, vrchat_config, mock_osc_client):
        """测试连接"""
        adapter = VRChatAdapter(vrchat_config)

        result = await adapter.connect()

        assert result is True
        assert adapter.is_connected is True
        assert adapter.osc_client is not None

    @pytest.mark.asyncio
    async def test_disconnect(self, vrchat_config, mock_osc_client):
        """测试断开连接"""
        adapter = VRChatAdapter(vrchat_config)
        await adapter.connect()

        result = await adapter.disconnect()

        assert result is True
        assert adapter.is_connected is False
        assert adapter.osc_client is None

    @pytest.mark.asyncio
    async def test_set_parameters(self, vrchat_config, mock_osc_client):
        """测试设置参数"""
        adapter = VRChatAdapter(vrchat_config)
        await adapter.connect()

        params = {"smile": 0.8, "eye_open": 0.5}

        result = await adapter.set_parameters(params)

        assert result is True
        # 验证发送了 OSC 消息
        assert mock_osc_client.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_set_parameters_not_connected(self, vrchat_config):
        """测试未连接时设置参数"""
        adapter = VRChatAdapter(vrchat_config)

        params = {"smile": 0.8}

        result = await adapter.set_parameters(params)

        assert result is False

    @pytest.mark.asyncio
    async def test_trigger_hotkey_valid(self, vrchat_config, mock_osc_client):
        """测试触发有效手势"""
        adapter = VRChatAdapter(vrchat_config)
        await adapter.connect()

        result = await adapter.trigger_hotkey("Wave")

        assert result is True
        # 验证发送了 VRCEmote 参数
        mock_osc_client.send_message.assert_called_with("/avatar/parameters/VRCEmote", 1)

    @pytest.mark.asyncio
    async def test_trigger_hotkey_invalid(self, vrchat_config, mock_osc_client):
        """测试触发无效手势"""
        adapter = VRChatAdapter(vrchat_config)
        await adapter.connect()

        result = await adapter.trigger_hotkey("InvalidGesture")

        assert result is False
        # 不应该发送 OSC 消息
        mock_osc_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_hotkey_not_connected(self, vrchat_config):
        """测试未连接时触发手势"""
        adapter = VRChatAdapter(vrchat_config)

        result = await adapter.trigger_hotkey("Wave")

        assert result is False

    def test_register_gesture(self, vrchat_config):
        """测试注册手势"""
        adapter = VRChatAdapter(vrchat_config)

        adapter.register_gesture("CustomGesture")

        assert "CustomGesture" in adapter._registered_gestures

    def test_get_registered_gestures(self, vrchat_config):
        """测试获取已注册手势"""
        adapter = VRChatAdapter(vrchat_config)

        adapter.register_gesture("Wave")
        adapter.register_gesture("Peace")

        gestures = adapter.get_registered_gestures()

        assert gestures == {"Wave", "Peace"}

    def test_get_supported_gestures(self, vrchat_config):
        """测试获取支持的手势列表"""
        adapter = VRChatAdapter(vrchat_config)

        gestures = adapter.get_supported_gestures()

        assert len(gestures) == 9
        assert "Neutral" in gestures
        assert "Wave" in gestures
        assert "Peace" in gestures
        assert "ThumbsUp" in gestures
        assert "RocknRoll" in gestures
        assert "HandGun" in gestures
        assert "Point" in gestures
        assert "Victory" in gestures
        assert "Cross" in gestures

    @pytest.mark.asyncio
    async def test_send_osc_not_connected(self, vrchat_config):
        """测试未连接时发送 OSC 消息"""
        adapter = VRChatAdapter(vrchat_config)

        result = await adapter._send_osc("/avatar/parameters/Test", 0.5)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_osc_success(self, vrchat_config, mock_osc_client):
        """测试成功发送 OSC 消息"""
        adapter = VRChatAdapter(vrchat_config)
        await adapter.connect()

        result = await adapter._send_osc("/avatar/parameters/Test", 0.5)

        assert result is True
        mock_osc_client.send_message.assert_called_once_with("/avatar/parameters/Test", 0.5)

    def test_param_translation_comprehensive(self, vrchat_config):
        """测试完整的参数翻译映射"""
        adapter = VRChatAdapter(vrchat_config)

        # 测试所有已定义的映射
        abstract_params = {
            "smile": 0.8,
            "eye_open": 0.5,
            "mouth_open": 0.3,
            "brow_down": 0.2,
            "brow_up": 0.1,
            "brow_angry": 0.9,
            "eye_x": 0.5,
            "eye_y": 0.5,
        }

        vrc_params = adapter.translate_params(abstract_params)

        assert vrc_params["MouthSmile"] == 0.8
        assert vrc_params["EyeOpen"] == 0.5
        assert vrc_params["MouthOpen"] == 0.3
        assert vrc_params["BrowDownLeft"] == 0.2
        assert vrc_params["BrowUpLeft"] == 0.1
        assert vrc_params["BrowAngryLeft"] == 0.9
        assert vrc_params["EyeX"] == 0.5
        assert vrc_params["EyeY"] == 0.5
