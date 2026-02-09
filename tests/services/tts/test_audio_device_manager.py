"""
AudioDeviceManager 测试
"""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from src.services.tts.audio_device_manager import AudioDeviceManager, DEPENDENCIES_OK


@pytest.fixture
def manager():
    """创建音频设备管理器实例"""
    return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16)


class TestAudioDeviceManager:
    """测试 AudioDeviceManager"""

    def test_init(self, manager):
        """测试初始化"""
        assert manager.sample_rate == 32000
        assert manager.channels == 1
        assert manager.dtype == np.int16
        assert manager.output_device_name is None
        assert manager.output_device_index is None
        assert not manager.is_playing

    @patch("src.services.tts.audio_device_manager.sd")
    def test_find_device_index_by_name(self, mock_sd, manager):
        """测试通过名称查找设备索引"""
        device1 = MagicMock()
        device1.name = "Device 1"
        device1.max_output_channels = 2

        device2 = MagicMock()
        device2.name = "My Device"
        device2.max_output_channels = 2

        mock_sd.query_devices.return_value = [device1, device2]

        index = manager.find_device_index("My Device")

        assert index == 1

    @patch("src.services.tts.audio_device_manager.sd")
    def test_find_device_index_default(self, mock_sd, manager):
        """测试查找默认设备"""
        device = MagicMock()
        device.name = "Default Device"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]
        mock_sd.default.device = [0, 0]

        index = manager.find_device_index(None)

        assert index == 0

    @patch("src.services.tts.audio_device_manager.sd")
    def test_find_device_index_not_found(self, mock_sd, manager):
        """测试查找设备未找到"""
        device = MagicMock()
        device.name = "Device 1"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]
        mock_sd.default.device = [-1, -1]

        index = manager.find_device_index("Nonexistent Device")

        assert index is None

    @patch("src.services.tts.audio_device_manager.sd")
    def test_set_output_device_by_name(self, mock_sd, manager):
        """测试通过名称设置输出设备"""
        device = MagicMock()
        device.name = "My Device"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]

        manager.set_output_device(device_name="My Device")

        assert manager.output_device_name == "My Device"
        assert manager.output_device_index == 0

    @patch("src.services.tts.audio_device_manager.sd")
    def test_set_output_device_by_index(self, mock_sd, manager):
        """测试通过索引设置输出设备"""
        manager.set_output_device(device_index=5)

        assert manager.output_device_index == 5
        assert manager.output_device_name is None

    @patch("src.services.tts.audio_device_manager.sd")
    def test_list_output_devices(self, mock_sd, manager):
        """测试列出输出设备"""
        device1 = MagicMock()
        device1.name = "Device 1"
        device1.max_output_channels = 2
        device1.default_samplerate = 48000

        device2 = MagicMock()
        device2.name = "Device 2"
        device2.max_output_channels = 0  # 输入设备
        device2.default_samplerate = 48000

        device3 = MagicMock()
        device3.name = "Device 3"
        device3.max_output_channels = 2
        device3.default_samplerate = 44100

        mock_sd.query_devices.return_value = [device1, device2, device3]

        devices = manager.list_output_devices()

        assert len(devices) == 2
        assert devices[0]["name"] == "Device 1"
        assert devices[1]["name"] == "Device 3"

    @pytest.mark.asyncio
    @patch("src.services.tts.audio_device_manager.sd")
    @patch("src.services.tts.audio_device_manager.asyncio.sleep")
    async def test_play_audio_success(self, mock_sleep, mock_sd, manager):
        """测试播放音频成功"""
        audio_array = np.array([1, 2, 3, 4, 5], dtype=np.int16)

        await manager.play_audio(audio_array, samplerate=32000, device_index=0)

        mock_sd.play.assert_called_once()
        mock_sd.stop.assert_called()

    @pytest.mark.asyncio
    async def test_play_audio_no_dependencies(self, manager):
        """测试在没有依赖时播放音频"""
        if DEPENDENCIES_OK:
            pytest.skip("依赖已安装，跳过此测试")

        audio_array = np.array([1, 2, 3, 4, 5], dtype=np.int16)

        with pytest.raises(RuntimeError, match="音频依赖缺失"):
            await manager.play_audio(audio_array)

    @patch("src.services.tts.audio_device_manager.sd")
    def test_stop_audio(self, mock_sd, manager):
        """测试停止音频播放"""
        manager.stop_audio()

        mock_sd.stop.assert_called_once()

    @patch("src.services.tts.audio_device_manager.sd")
    def test_get_device_info(self, mock_sd, manager):
        """测试获取设备信息"""
        manager.output_device_index = 2
        mock_device = MagicMock()
        mock_device.name = "Test Device"
        mock_device.max_output_channels = 2
        mock_device.max_input_channels = 1
        mock_device.default_samplerate = 48000

        mock_sd.query_devices.return_value = mock_device

        info = manager.get_device_info()

        assert info["index"] == 2
        assert info["name"] == "Test Device"
        assert info["max_output_channels"] == 2
        assert info["max_input_channels"] == 1
        assert info["default_samplerate"] == 48000

    @patch("src.services.tts.audio_device_manager.sd")
    def test_get_device_info_default(self, mock_sd, manager):
        """测试获取默认设备信息"""
        mock_device = MagicMock()
        mock_device.name = "Default Device"
        mock_device.max_output_channels = 2
        mock_device.max_input_channels = 1
        mock_device.default_samplerate = 48000

        mock_sd.query_devices.return_value = mock_device
        mock_sd.default.device = [0, 0]

        info = manager.get_device_info()

        assert info["index"] == 0
        assert info["name"] == "Default Device"

    @patch("src.services.tts.audio_device_manager.sd")
    def test_get_device_info_no_default(self, mock_sd, manager):
        """测试在没有默认设备时获取设备信息"""
        mock_sd.query_devices.return_value = {}
        mock_sd.default.device = [-1, -1]

        info = manager.get_device_info()

        assert info is None
