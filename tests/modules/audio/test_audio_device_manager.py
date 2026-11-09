"""AudioDeviceManager：输出设备查找/设置/列举、播放停止、设备信息，以及流式预缓冲"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.audio import audio_device_manager as adm
from src.modules.audio.audio_device_manager import AudioDeviceManager
from tests.modules.audio.fakes import RecordingSink

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


def test_oversized_block_gap_logs_debug_only(sd, monkeypatch) -> None:
    """块间隔偏大只进诊断（debug）：逐句系统性触发，与听感断流无关，不进告警面"""
    clock = SimpleNamespace(t=1000.0)
    clock.monotonic = clock.perf_counter = lambda: clock.t
    monkeypatch.setattr(adm, "time", clock)  # 可控单调时钟
    manager = AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, prebuffer_seconds=0.0)
    manager.logger = MagicMock()
    manager.start_stream(utterance_id="gap")
    chunk = np.zeros(100, dtype=np.int16)

    manager.write_chunk(chunk)  # 块 1：预缓冲阈值为 0，立即开播，建立块间基线
    manager.logger.reset_mock()
    clock.t += 3.0
    manager.write_chunk(chunk)  # 块 2：毛间隔 3s，净间隔同为 3s

    manager.logger.debug.assert_called_once()
    assert "上游块间隔偏大" in str(manager.logger.debug.call_args)
    manager.logger.warning.assert_not_called()


class TestPlaybackStallDetection:
    """播放中断流检测：utterance 播放会话进行中且超阈值无新音频块才告警

    判定锚定"正在播放"而非"连接存活"：句间静默期没有播放会话，不告警；
    正常播放块间隔远小于阈值，零告警。检测方法直接调用（注入时钟控时间，
    不依赖后台巡检线程——stall_watchdog_enabled=False 关掉线程）。
    """

    THRESHOLD_MS = 5000

    @pytest.fixture
    def stall_setup(self, sd, monkeypatch) -> SimpleNamespace:
        clock = SimpleNamespace(t=1000.0)
        clock.monotonic = clock.perf_counter = lambda: clock.t
        monkeypatch.setattr(adm, "time", clock)
        manager = AudioDeviceManager(
            sample_rate=32000,
            channels=1,
            dtype=np.int16,
            prebuffer_seconds=0.0,
            stall_threshold_ms=self.THRESHOLD_MS,
            stall_watchdog_enabled=False,
        )
        manager.logger = MagicMock()
        chunk = np.zeros(100, dtype=np.int16)
        return SimpleNamespace(manager=manager, chunk=chunk, clock=clock, stream=sd.OutputStream.return_value)

    def test_normal_playback_with_short_block_gaps_alerts_nothing(self, stall_setup) -> None:
        """正常播放：块间隔远小于阈值（正常 <1s），整个播放会话零告警"""
        m = stall_setup.manager
        m.start_stream(utterance_id="normal")
        for _ in range(5):
            stall_setup.clock.t += 0.3  # 正常合成节奏：块间隔 <1s
            m.write_chunk(stall_setup.chunk)
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

        m.stop_stream()  # 收尾不告警
        m.logger.warning.assert_not_called()

    def test_stale_stream_over_threshold_while_playing_alerts_once(self, stall_setup) -> None:
        """播放中 >5s 无新块：warning 恰一条；后续巡检不重复；恢复到达后基线刷新"""
        m = stall_setup.manager
        m.start_stream(utterance_id="stall")
        m.write_chunk(stall_setup.chunk)
        m.logger.reset_mock()

        stall_setup.clock.t += 6.0  # 超过阈值无任何新块（上游停供，写块不再被调）
        assert m.check_playback_stall() is True
        m.logger.warning.assert_called_once()
        assert "播放中断流" in str(m.logger.warning.call_args)

        stall_setup.clock.t += 3.0  # 已告警过：不重复刷屏
        assert m.check_playback_stall() is False
        m.logger.warning.assert_called_once()

        stall_setup.clock.t += 0.1
        m.write_chunk(stall_setup.chunk)  # 数据恢复到达：基线刷新，不再告警
        assert m.check_playback_stall() is False
        m.logger.warning.assert_called_once()

        m.stop_stream()

    def test_inter_sentence_silence_over_threshold_alerts_nothing(self, stall_setup) -> None:
        """句间静默：播放会话已关停（stop_stream），无论过多久零告警"""
        m = stall_setup.manager
        m.start_stream(utterance_id="done")
        m.write_chunk(stall_setup.chunk)
        m.stop_stream()  # 句子播完，监控会话结束
        m.logger.reset_mock()

        stall_setup.clock.t += 30.0  # 句间静默远超阈值
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

    def test_whole_sentence_without_any_block_still_judged(self, stall_setup) -> None:
        """整句一个块都没来也落在判定范围内（旧告警的盲区：写块不被调用即永不触发）"""
        m = stall_setup.manager
        m.start_stream(utterance_id="silent")
        stall_setup.clock.t += 6.0  # 开播后无任何块到达
        assert m.check_playback_stall() is True
        assert "播放中断流" in str(m.logger.warning.call_args)
        m.stop_stream()

    def test_write_backpressure_does_not_trigger_false_stall_alert(self, stall_setup) -> None:
        """写声卡背压（数据过剩等播放消费）从间隔中扣除，不误报成断流"""
        m = stall_setup.manager
        clock = stall_setup.clock
        m.start_stream(utterance_id="backpressure")
        m.write_chunk(stall_setup.chunk)  # 预缓冲阈值为 0：首块即开播
        m.logger.reset_mock()

        # 下一块写声卡阻塞 6s（时钟随阻塞推进）：阻塞期间上游送不进新块，
        # 毛间隔超阈值，但全部是背压
        stall_setup.stream.write.side_effect = lambda _chunk: setattr(clock, "t", clock.t + 6.0)
        clock.t += 0.1
        m.write_chunk(stall_setup.chunk)
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

        m.stop_stream()


class TestSinkFeedAtSoundcardWrite:
    """分接递入紧贴写声卡：到达（含整段突发）不递，写声卡才递且顺序一致

    背景：随"到达"递入会让口型分析器的缓冲直接跳到句尾，短句句身整段
    被消费方跳过（口型不动的根因）。
    """

    @pytest.fixture
    def tapped(self, sd) -> SimpleNamespace:
        sink = RecordingSink()
        manager = AudioDeviceManager(sample_rate=32000, sink=sink, prebuffer_seconds=0.05)
        return SimpleNamespace(sink=sink, manager=manager, stream=sd.OutputStream.return_value)

    @staticmethod
    def _fed_total(sink: RecordingSink) -> int:
        return sum(len(chunk) for chunk in sink.fed_chunks())

    def test_arrival_does_not_feed_write_does(self, tapped) -> None:
        """预缓冲期到达不递入；收尾冲播写声卡时才按序整段递出"""
        tapped.manager.prebuffer_seconds = 100.0  # 测试内不会触达阈值
        chunks = [np.full(1600, i, dtype=np.int16) for i in (1, 2, 3)]
        tapped.manager.start_stream(utterance_id="tap")
        for c in chunks:
            tapped.manager.write_chunk(c)  # 全部进 pending：到达不递入
        assert self._fed_total(tapped.sink) == 0

        tapped.manager.stop_stream()  # 收尾冲播：写声卡时按序递出

        assert self._fed_total(tapped.sink) == 4800
        np.testing.assert_array_equal(np.concatenate(tapped.sink.fed_chunks()), np.concatenate(chunks))
        assert tapped.stream.write.call_count == len(tapped.sink.fed_chunks())
        tapped.stream.stop.assert_called_once()

    def test_stream_writes_carry_their_audio_to_the_sink(self, tapped) -> None:
        """开播后每块写声卡都伴随一次同块分接递入，总量守恒且不重复"""
        chunk = np.arange(1600, dtype=np.int16)
        tapped.manager.start_stream(utterance_id="tap")
        for i in range(3):
            tapped.manager.write_chunk(chunk + i)  # 首块达阈值冲播，后两块直写

        assert self._fed_total(tapped.sink) == 4800
        np.testing.assert_array_equal(
            np.concatenate(tapped.sink.fed_chunks()), np.concatenate([chunk + i for i in range(3)])
        )
        assert tapped.stream.write.call_count == len(tapped.sink.fed_chunks())

        tapped.manager.stop_stream()
        assert self._fed_total(tapped.sink) == 4800  # 收尾不重复递入

    def test_discard_leaves_sink_untouched(self, tapped) -> None:
        """取消/截断：未播的预缓冲被丢弃，不发声也不递分接"""
        tapped.manager.prebuffer_seconds = 100.0
        tapped.manager.start_stream(utterance_id="drop")
        tapped.manager.write_chunk(np.full(1600, 9, dtype=np.int16))

        tapped.manager.stop_stream(discard_pending=True)

        assert self._fed_total(tapped.sink) == 0
        tapped.stream.stop.assert_called_once()
