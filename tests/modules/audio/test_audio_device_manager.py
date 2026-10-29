"""AudioDeviceManager：输出设备查找/设置/列举、播放停止、设备信息，以及流式预缓冲"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.audio import audio_device_manager as adm
from src.modules.audio.audio_device_manager import AudioDeviceManager

# 无音频子系统的环境（如无头 CI）下依赖降级，本文件的 sd 假件挂载点不存在
pytestmark = pytest.mark.skipif(
    not adm.DEPENDENCIES_OK,
    reason="sounddevice 依赖不可用（无音频子系统环境），音频管理器走降级路径",
)

SD = "src.modules.audio.audio_device_manager.sd"


def _device(name: str, out: int = 2, inp: int = 0, rate: int = 48000) -> SimpleNamespace:
    return SimpleNamespace(name=name, max_output_channels=out, max_input_channels=inp, default_samplerate=rate)


@pytest.fixture
def manager() -> AudioDeviceManager:
    return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16)


@pytest.fixture
def sd():
    with patch(SD) as mock_sd:
        yield mock_sd


def test_init_has_no_output_device_selected(manager) -> None:
    assert (manager.sample_rate, manager.channels, manager.dtype) == (32000, 1, np.int16)
    assert manager.output_device_name is None and manager.output_device_index is None
    assert not manager.is_playing


@pytest.mark.parametrize(
    ("query", "default_device", "expected"),
    [
        ("My Device", [-1, -1], 1),  # 按名称匹配
        (None, [0, 0], 0),  # 未指定名称：取系统默认输出设备
        ("Nonexistent Device", [-1, -1], None),
    ],
)
def test_find_device_index(manager, sd, query, default_device, expected) -> None:
    sd.query_devices.return_value = [_device("Device 1"), _device("My Device")]
    sd.default.device = default_device

    assert manager.find_device_index(query) == expected


def test_set_output_device_by_name_resolves_the_index_and_by_index_keeps_name_empty(manager, sd) -> None:
    sd.query_devices.return_value = [_device("My Device")]
    manager.set_output_device(device_name="My Device")
    assert (manager.output_device_name, manager.output_device_index) == ("My Device", 0)

    manager.set_output_device(device_index=5)
    assert manager.output_device_index == 5 and manager.output_device_name is None


def test_list_output_devices_skips_input_only_devices(manager, sd) -> None:
    sd.query_devices.return_value = [_device("Device 1"), _device("Device 2", out=0, inp=2), _device("Device 3")]

    assert [d["name"] for d in manager.list_output_devices()] == ["Device 1", "Device 3"]


async def test_play_audio_starts_then_stops_the_sound_card(manager, sd) -> None:
    with patch("src.modules.audio.audio_device_manager.asyncio.sleep"):
        await manager.play_audio(np.array([1, 2, 3, 4, 5], dtype=np.int16), samplerate=32000, device_index=0)

    sd.play.assert_called_once()
    sd.stop.assert_called()
    manager.stop_audio()
    assert sd.stop.called


def test_get_device_info_uses_selected_then_default_device_and_none_without_either(manager, sd) -> None:
    sd.query_devices.return_value = _device("Test Device", inp=1)
    manager.output_device_index = 2
    assert manager.get_device_info() == {
        "index": 2,
        "name": "Test Device",
        "max_output_channels": 2,
        "max_input_channels": 1,
        "default_samplerate": 48000,
    }

    manager.output_device_index = None  # 未选择：回落系统默认设备
    sd.default.device = [0, 0]
    sd.query_devices.return_value = _device("Default Device", inp=1)
    info = manager.get_device_info()
    assert (info["index"], info["name"]) == (0, "Default Device")

    sd.query_devices.return_value = {}  # 没有默认设备
    sd.default.device = [-1, -1]
    assert manager.get_device_info() is None


class TestStreamPrebuffer:
    """流式预缓冲：攒够阈值才开播声卡流，吸收上游短暂停顿防欠载"""

    @pytest.fixture
    def big_manager(self) -> AudioDeviceManager:
        """超大预缓冲：测试内不会触达阈值"""
        return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=100.0)

    def test_holds_stream_until_threshold_then_flushes_pending_before_going_direct(self, sd, big_manager) -> None:
        big_manager.start_stream(utterance_id="t1")
        stream = sd.OutputStream.return_value
        sd.OutputStream.assert_called_once()
        chunk = np.zeros(1600, dtype=np.int16)

        big_manager.write_chunk(chunk)
        big_manager.write_chunk(chunk)
        stream.start.assert_not_called()  # 未达阈值：流已创建但不开播，块暂存 pending
        assert len(big_manager._pending_chunks) == 2 and big_manager.is_playing is True

        big_manager.prebuffer_seconds = 0.05  # 阈值调小后下一块达标：开播并把 pending 连同本块冲入
        big_manager.write_chunk(chunk)
        stream.start.assert_called_once()
        assert stream.write.call_count == 3 and big_manager._pending_chunks == []

        big_manager.write_chunk(chunk)  # 之后的块直接写声卡
        assert stream.write.call_count == 4

    def test_stream_that_fails_to_open_leaves_the_sentence_silent(self, sd, big_manager) -> None:
        sd.OutputStream.side_effect = RuntimeError("声卡被占用")
        assert big_manager.open_output_stream() is None  # 记日志返回 None，不把异常抛进播放流程
        big_manager.start_prepared_stream(None, utterance_id="t2")
        big_manager.write_chunk(np.zeros(1600, dtype=np.int16))
        assert big_manager._stream is None and big_manager.is_playing is False

    @pytest.mark.parametrize("discard", [False, True])
    def test_stop_stream_flushes_the_residual_unless_discarded(self, sd, big_manager, discard) -> None:
        """正常收尾把不足阈值的残余冲播（短句兜底）；取消/截断则丢弃未播预缓冲，不发声"""
        big_manager.start_stream(utterance_id="t3")
        stream = sd.OutputStream.return_value
        big_manager.write_chunk(np.zeros(1600, dtype=np.int16))

        big_manager.stop_stream(discard_pending=discard)

        assert stream.start.call_count == stream.write.call_count == (0 if discard else 1)
        stream.stop.assert_called_once()
        assert big_manager._stream is None and big_manager.is_playing is False


@pytest.mark.parametrize(
    ("write_backpressure_s", "idle_s", "alerts"),
    [(0.0, 3.0, True), (2.5, 0.5, False)],
    ids=["real_gap", "backpressure_only"],
)
def test_supply_gap_alert_subtracts_write_backpressure(
    sd, monkeypatch, write_backpressure_s: float, idle_s: float, alerts: bool
) -> None:
    """块间毛间隔同为 3s：上游真断供才告警；写等播放消费的背压（数据过剩）不能误报成断流。"""
    clock = SimpleNamespace(t=1000.0)
    clock.monotonic = clock.perf_counter = lambda: clock.t
    monkeypatch.setattr(adm, "time", clock)  # 可控单调时钟
    manager = AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=0.0)
    manager.logger = MagicMock()
    manager.start_stream(utterance_id="gap")
    chunk = np.zeros(100, dtype=np.int16)

    manager.write_chunk(chunk)  # 块 1：预缓冲阈值为 0，立即开播，建立块间基线
    sd.OutputStream.return_value.write.side_effect = lambda _chunk: setattr(clock, "t", clock.t + write_backpressure_s)
    clock.t += 0.5
    manager.write_chunk(chunk)  # 块 2：写入阻塞 write_backpressure_s（时钟随阻塞推进）
    manager.logger.reset_mock()
    clock.t += idle_s
    manager.write_chunk(chunk)  # 块 3：毛间隔 = 背压 + 空闲，净间隔 = 空闲

    warned = any("供给断流" in str(call) for call in manager.logger.warning.call_args_list)
    assert warned is alerts
