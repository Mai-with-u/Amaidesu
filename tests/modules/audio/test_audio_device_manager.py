"""
AudioDeviceManager 测试
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.audio.audio_device_manager import AudioDeviceManager


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

    @patch("src.modules.audio.audio_device_manager.sd")
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

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_find_device_index_default(self, mock_sd, manager):
        """测试查找默认设备"""
        device = MagicMock()
        device.name = "Default Device"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]
        mock_sd.default.device = [0, 0]

        index = manager.find_device_index(None)

        assert index == 0

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_find_device_index_not_found(self, mock_sd, manager):
        """测试查找设备未找到"""
        device = MagicMock()
        device.name = "Device 1"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]
        mock_sd.default.device = [-1, -1]

        index = manager.find_device_index("Nonexistent Device")

        assert index is None

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_set_output_device_by_name(self, mock_sd, manager):
        """测试通过名称设置输出设备"""
        device = MagicMock()
        device.name = "My Device"
        device.max_output_channels = 2

        mock_sd.query_devices.return_value = [device]

        manager.set_output_device(device_name="My Device")

        assert manager.output_device_name == "My Device"
        assert manager.output_device_index == 0

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_set_output_device_by_index(self, mock_sd, manager):
        """测试通过索引设置输出设备"""
        manager.set_output_device(device_index=5)

        assert manager.output_device_index == 5
        assert manager.output_device_name is None

    @patch("src.modules.audio.audio_device_manager.sd")
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

    @patch("src.modules.audio.audio_device_manager.sd")
    @patch("src.modules.audio.audio_device_manager.asyncio.sleep")
    async def test_play_audio_success(self, mock_sleep, mock_sd, manager):
        """测试播放音频成功"""
        audio_array = np.array([1, 2, 3, 4, 5], dtype=np.int16)

        await manager.play_audio(audio_array, samplerate=32000, device_index=0)

        mock_sd.play.assert_called_once()
        mock_sd.stop.assert_called()

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_stop_audio(self, mock_sd, manager):
        """测试停止音频播放"""
        manager.stop_audio()

        mock_sd.stop.assert_called_once()

    @patch("src.modules.audio.audio_device_manager.sd")
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

    @patch("src.modules.audio.audio_device_manager.sd")
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

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_get_device_info_no_default(self, mock_sd, manager):
        """测试在没有默认设备时获取设备信息"""
        mock_sd.query_devices.return_value = {}
        mock_sd.default.device = [-1, -1]

        info = manager.get_device_info()

        assert info is None


class TestStreamPrebuffer:
    """流式预缓冲：攒够阈值才开播声卡流，吸收上游短暂停顿防欠载"""

    @pytest.fixture
    def small_manager(self):
        """2 块即达阈值的预缓冲（便于测试）"""
        return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=0.001)

    @pytest.fixture
    def big_manager(self):
        """超大预缓冲（测试内不会触达阈值）"""
        return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=100.0)

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_prebuffer_holds_stream_until_threshold(self, mock_sd, big_manager):
        """未达预缓冲阈值：OutputStream 创建但不开播，块暂存 pending"""
        big_manager.start_stream(utterance_id="t1")
        mock_sd.OutputStream.assert_called_once()
        mock_sd.OutputStream.return_value.start.assert_not_called()

        chunk = np.zeros(1600, dtype=np.int16)
        big_manager.write_chunk(chunk)
        big_manager.write_chunk(chunk)

        mock_sd.OutputStream.return_value.start.assert_not_called()
        assert len(big_manager._pending_chunks) == 2
        assert big_manager.is_playing is True

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_prebuffer_flushes_on_threshold(self, mock_sd, big_manager):
        """把 prebuffer 调小后写入达阈值：开播并把 pending 一次性冲入"""
        big_manager.prebuffer_seconds = 0.1
        big_manager.start_stream(utterance_id="t2")
        stream = mock_sd.OutputStream.return_value

        chunk = np.zeros(3200, dtype=np.int16)  # 0.1s @32k，一块达阈值
        big_manager.write_chunk(chunk)

        stream.start.assert_called_once()
        stream.write.assert_called_once()
        assert big_manager._pending_chunks == []
        # 后续块直接写声卡
        big_manager.write_chunk(chunk)
        assert stream.write.call_count == 2

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_stop_stream_flushes_residual(self, mock_sd, big_manager):
        """正常收尾：不足阈值的残余先冲播（短句兜底）"""
        big_manager.start_stream(utterance_id="t3")
        stream = mock_sd.OutputStream.return_value
        big_manager.write_chunk(np.zeros(1600, dtype=np.int16))

        big_manager.stop_stream()

        stream.start.assert_called_once()
        stream.write.assert_called_once()
        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        assert big_manager._stream is None
        assert big_manager.is_playing is False

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_stop_stream_discard_pending_skips_flush(self, mock_sd, big_manager):
        """取消/截断语义：丢弃未播预缓冲，不冲播、不发声"""
        big_manager.start_stream(utterance_id="t4")
        stream = mock_sd.OutputStream.return_value
        big_manager.write_chunk(np.zeros(1600, dtype=np.int16))

        big_manager.stop_stream(discard_pending=True)

        stream.start.assert_not_called()
        stream.write.assert_not_called()
        stream.stop.assert_called_once()
        assert big_manager._stream is None


class TestSupplyGapAlert:
    """供给断流告警：间隔扣除写背压后才判定真断流（防背压假阳性）"""

    def _manager_with_fake_clock(self):
        """prebuffer 100s（write 直写路径）+ 可控单调时钟"""
        from src.modules.audio import audio_device_manager as adm

        manager = AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=100.0)
        manager.logger = MagicMock()
        clock = {"t": 1000.0}
        self.clock = clock
        patcher = patch.object(adm, "time")
        mock_time = patcher.start()
        self.addCleanup = patcher.stop
        mock_time.monotonic = lambda: clock["t"]
        mock_time.perf_counter = lambda: clock["t"]
        return manager

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_real_supply_gap_alerts(self, mock_sd):
        """两块间隔 3s 且写无阻塞：净间隔 3s > 2s，告警真断流"""
        import time as real_time

        manager = self._manager_with_fake_clock()
        self.clock["t"] = 1000.0
        manager.start_stream(utterance_id="g1")
        manager.write_chunk(np.zeros(100, dtype=np.int16))
        self.clock["t"] += 3.0  # 3s 后下一块，期间无写阻塞
        manager.write_chunk(np.zeros(100, dtype=np.int16))

        warnings = [str(c) for c in manager.logger.warning.call_args_list]
        assert any("供给断流" in t for t in warnings)
        real_time.sleep(0)

    @patch("src.modules.audio.audio_device_manager.sd")
    def test_write_backpressure_not_reported_as_gap(self, mock_sd):
        """写背压不计入断流：块2 的 write 等播放消费阻塞 2.5s（时钟随阻塞
        推进），块3 到达时毛间隔 3.0s 扣除背压 2.5s 后净 0.5s，不告警"""
        manager = self._manager_with_fake_clock()
        manager.start_stream(utterance_id="g2")
        stream = mock_sd.OutputStream.return_value

        # 块1：基线（建立 last_feed 与写阻塞基线）
        self.clock["t"] += 1.0
        manager.write_chunk(np.zeros(100, dtype=np.int16))

        # 块2：write 阻塞 2.5s（缓冲满，等播放消费腾空间——背压）
        def slow_write(chunk):
            self.clock["t"] += 2.5

        self.clock["t"] += 0.5
        stream.write.side_effect = slow_write
        manager.write_chunk(np.zeros(100, dtype=np.int16))
        manager.logger.reset_mock()

        # 块3：到达（毛间隔 3.0s，其中 2.5s 是块2 的写背压）
        self.clock["t"] += 0.5
        stream.write.side_effect = None
        manager.write_chunk(np.zeros(100, dtype=np.int16))

        warnings = [str(c) for c in manager.logger.warning.call_args_list]
        assert not any("供给断流" in t for t in warnings)
