"""AudioDeviceManager：输出设备查找/设置/列举、回调流缓冲与欠载静音兜底、并发取消与分接"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.audio import audio_device_manager as adm
from src.modules.audio.audio_device_manager import AudioDeviceManager
from tests.modules.audio.fakes import RecordingSink, make_fake_sounddevice, stop_and_drain

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
    return AudioDeviceManager(sample_rate=32000, channels=1, dtype=np.int16, stall_watchdog_enabled=False)


@pytest.fixture
def sd():
    fake = make_fake_sounddevice()
    with patch(SD, fake):
        yield fake


def test_init_has_no_output_device_selected(manager) -> None:
    assert (manager.sample_rate, manager.channels, manager.dtype) == (32000, 1, np.int16)
    assert manager.output_device_name is None and manager.output_device_index is None
    assert not manager.is_playing
    assert not manager.is_stream_started
    assert manager.buffered_frames == 0


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


class TestCallbackStreamBuffering:
    """回调流持续预缓冲：攒够阈值开播后队列仍保留水位，句中欠载自动补零静音"""

    @pytest.fixture
    def big_manager(self) -> AudioDeviceManager:
        """超大预缓冲 + 禁用看门狗：测试步进完全可控"""
        return AudioDeviceManager(
            sample_rate=32000,
            channels=1,
            dtype=np.int16,
            prebuffer_seconds=100.0,
            stall_watchdog_enabled=False,
        )

    def test_holds_stream_until_threshold_then_keeps_queue_as_continuous_buffer(self, sd, big_manager) -> None:
        big_manager.start_stream(utterance_id="t1")
        stream = sd.created_streams[-1]
        chunk = np.arange(1, 1601, dtype=np.int16)

        big_manager.write_chunk(chunk)
        big_manager.write_chunk(chunk)
        assert stream.start_count == 0
        assert not big_manager.is_stream_started
        assert big_manager.is_playing is True
        assert big_manager.buffered_frames == 3200

        # 阈值调小后下一块达标：开播，但队列保留全部 4800 帧由回调逐块消费（非一次性写干）
        big_manager.prebuffer_seconds = 0.05
        big_manager.write_chunk(chunk)
        assert stream.start_count == 1
        assert big_manager.is_stream_started is True
        assert big_manager.buffered_frames == 4800

        # 回调拉走 1600 帧后余量同步扣减，再写入下一块继续累加
        stream.pull(1600)
        assert big_manager.buffered_frames == 3200
        big_manager.write_chunk(chunk)
        assert big_manager.buffered_frames == 4800

        stop_and_drain(big_manager, stream, blocksize=1600)
        assert big_manager.buffered_frames == 0
        assert big_manager.is_playing is False
        assert stream.method_calls == ["start", "stop", "close"]

    def test_stream_that_fails_to_open_leaves_the_sentence_silent(self, sd, big_manager) -> None:
        sd.OutputStream.side_effect = RuntimeError("声卡被占用")
        assert big_manager.open_output_stream() is None  # 记日志返回 None，不把异常抛进播放流程
        big_manager.start_prepared_stream(None, utterance_id="t2")
        big_manager.write_chunk(np.zeros(1600, dtype=np.int16))
        assert big_manager.is_playing is False
        assert big_manager.buffered_frames == 0

    @pytest.mark.parametrize("discard", [False, True])
    def test_stop_stream_drains_short_utterance_unless_discarded(self, sd, big_manager, discard) -> None:
        """正常收尾把不足阈值的短句开播排空；取消/截断则 abort 并丢弃缓冲，不发声"""
        big_manager.start_stream(utterance_id="t3")
        stream = sd.created_streams[-1]
        chunk = np.full(1600, 12, dtype=np.int16)
        big_manager.write_chunk(chunk)

        if discard:
            big_manager.stop_stream(discard_pending=True)
            assert stream.start_count == 0
            assert stream.abort_count == 1
            assert stream.close_count == 1
        else:
            stop_and_drain(big_manager, stream, blocksize=1600)
            assert stream.start_count == 1
            assert stream.stop_count == 1
            assert stream.close_count == 1
            np.testing.assert_array_equal(stream.played_audio(), chunk)

        assert big_manager.is_playing is False
        assert big_manager.buffered_frames == 0

    def test_mid_stream_underrun_fills_zeros_alerts_once_and_recovers(self, sd) -> None:
        """句中欠载回调显式输出零静音防止驱动怪叫，实时告警并支持后续音频块无缝恢复"""
        manager = AudioDeviceManager(
            sample_rate=32000,
            channels=1,
            dtype=np.int16,
            prebuffer_seconds=0.05,
            stall_watchdog_enabled=False,
        )
        manager.logger = MagicMock()
        manager.start_stream(utterance_id="utt_underrun")
        stream = sd.created_streams[-1]

        first = np.full(1600, 10, dtype=np.int16)
        manager.write_chunk(first)
        assert stream.start_count == 1

        # 消费完首段后发生 800 帧句中欠载
        stream.pull(1600)
        silent_out, stopped = stream.pull(800)
        assert stopped is False
        assert np.all(silent_out == 0)

        manager.logger.reset_mock()
        assert manager.check_playback_underrun() is True
        assert manager.check_playback_underrun() is False  # 同一次事件不重复告警
        manager.logger.warning.assert_called_once()
        assert "声卡播放欠载" in str(manager.logger.warning.call_args)

        # 上游恢复送块并正常排空收尾：停流汇报最终欠载统计
        second = np.full(1600, 20, dtype=np.int16)
        manager.write_chunk(second)
        manager.logger.reset_mock()
        stop_and_drain(manager, stream, blocksize=1600)

        stats = manager.stream_underrun_stats
        assert stats is not None and stats.events == 1 and stats.frames == 800 and stats.played_frames == 3200
        manager.logger.warning.assert_called_once()
        assert "流式播放结束含欠载" in str(manager.logger.warning.call_args)

        played = stream.played_audio()
        np.testing.assert_array_equal(played[:1600], first)
        assert np.all(played[1600:2400] == 0)
        np.testing.assert_array_equal(played[2400:4000], second)

    def test_concurrent_discard_wakes_draining_stop_and_aborts_cleanly(self, sd) -> None:
        """正常排空等待期间收到取消截断：abort 立刻唤醒排空线程，零死锁、零双停分接"""
        sink = RecordingSink()
        manager = AudioDeviceManager(
            sample_rate=32000,
            channels=1,
            dtype=np.int16,
            sink=sink,
            prebuffer_seconds=0.05,
            stall_watchdog_enabled=False,
        )
        manager.start_stream(utterance_id="utt_abort_race")
        stream = sd.created_streams[-1]
        manager.write_chunk(np.full(9600, 5, dtype=np.int16))
        assert manager.is_stream_started

        drain_errors: list[BaseException] = []

        def _drain() -> None:
            try:
                manager.stop_stream(discard_pending=False)
            except BaseException as exc:  # noqa: BLE001 - 收集子线程异常
                drain_errors.append(exc)

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()

        deadline = time.monotonic() + 2.0
        while drain_thread.is_alive() and not manager.is_stream_draining:
            if time.monotonic() >= deadline:
                raise AssertionError("等待排空状态超时")
            drain_thread.join(timeout=0.001)

        # 不拉空声卡缓冲，直接由取消线程触发硬截断：排空线程必须立即被 abort 唤醒退出
        manager.stop_stream(discard_pending=True)
        drain_thread.join(timeout=1.0)

        assert not drain_thread.is_alive()
        assert not drain_errors
        assert stream.abort_count == 1
        assert stream.close_count == 1
        assert manager.is_playing is False
        assert [c[0] for c in sink.calls].count("stop") == 1


def test_oversized_block_gap_logs_debug_only(sd) -> None:
    """块间隔偏大只进诊断（debug）：不误入告警面"""
    now_holder = [1000.0]
    manager = AudioDeviceManager(
        sample_rate=32000,
        channels=1,
        dtype=np.int16,
        prebuffer_seconds=0.0,
        stall_watchdog_enabled=False,
        clock=lambda: now_holder[0],
    )
    manager.logger = MagicMock()
    manager.start_stream(utterance_id="gap")
    chunk = np.zeros(100, dtype=np.int16)

    manager.write_chunk(chunk)  # 块 1：预缓冲阈值为 0，立即开播，建立块间基线
    manager.logger.reset_mock()
    now_holder[0] += 3.0
    manager.write_chunk(chunk)  # 块 2：间隔 3s

    manager.logger.debug.assert_called_once()
    assert "上游块间隔偏大" in str(manager.logger.debug.call_args)
    manager.logger.warning.assert_not_called()
    manager.stop_stream(discard_pending=True)


class TestPlaybackStallDetection:
    """播放中断流检测：声卡流已开播、未 EOS 且扣除缓冲余量后仍超阈值无新块才告警"""

    THRESHOLD_MS = 5000

    @pytest.fixture
    def stall_setup(self, sd) -> SimpleNamespace:
        now_holder = [1000.0]
        manager = AudioDeviceManager(
            sample_rate=32000,
            channels=1,
            dtype=np.int16,
            prebuffer_seconds=0.0,
            stall_threshold_ms=self.THRESHOLD_MS,
            stall_watchdog_enabled=False,
            clock=lambda: now_holder[0],
        )
        manager.logger = MagicMock()
        chunk = np.zeros(100, dtype=np.int16)
        return SimpleNamespace(
            manager=manager,
            chunk=chunk,
            now_holder=now_holder,
            sd=sd,
        )

    def test_normal_playback_with_short_block_gaps_alerts_nothing(self, stall_setup) -> None:
        """正常播放：块间隔远小于阈值（正常 <1s），整个播放会话零告警"""
        m = stall_setup.manager
        m.start_stream(utterance_id="normal")
        stream = stall_setup.sd.created_streams[-1]
        for _ in range(5):
            stall_setup.now_holder[0] += 0.3
            m.write_chunk(stall_setup.chunk)
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

        stop_and_drain(m, stream, blocksize=1600)
        m.logger.warning.assert_not_called()

    def test_stale_stream_over_threshold_while_playing_alerts_once(self, stall_setup) -> None:
        """播放中缓冲耗尽后 >5s 无新块：warning 恰一条；后续巡检不重复；恢复到达后刷新基线"""
        m = stall_setup.manager
        m.start_stream(utterance_id="stall")
        stream = stall_setup.sd.created_streams[-1]
        m.write_chunk(stall_setup.chunk)
        stream.pull(100)
        m.logger.reset_mock()

        stall_setup.now_holder[0] += 6.0  # 超过阈值无任何新块且缓冲已空
        assert m.check_playback_stall() is True
        m.logger.warning.assert_called_once()
        assert "播放中断流" in str(m.logger.warning.call_args)

        stall_setup.now_holder[0] += 3.0  # 已告警过：不重复刷屏
        assert m.check_playback_stall() is False
        m.logger.warning.assert_called_once()

        stall_setup.now_holder[0] += 0.1
        m.write_chunk(stall_setup.chunk)  # 数据恢复到达：基线刷新，不再告警
        assert m.check_playback_stall() is False
        m.logger.warning.assert_called_once()

        m.stop_stream(discard_pending=True)

    def test_inter_sentence_silence_over_threshold_alerts_nothing(self, stall_setup) -> None:
        """句间静默：播放会话已关停（stop_stream），无论过多久零告警"""
        m = stall_setup.manager
        m.start_stream(utterance_id="done")
        stream = stall_setup.sd.created_streams[-1]
        m.write_chunk(stall_setup.chunk)
        stop_and_drain(m, stream, blocksize=1600)
        m.logger.reset_mock()

        stall_setup.now_holder[0] += 30.0
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

    def test_session_open_without_soundcard_start_alerts_nothing(self, stall_setup) -> None:
        """播放会话已开启但声卡流尚未开播：静默超阈值零告警"""
        m = stall_setup.manager
        m.prebuffer_seconds = 100.0
        m.start_stream(utterance_id="silent")
        stall_setup.now_holder[0] += 6.0
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()
        m.stop_stream(discard_pending=True)

    def test_buffered_audio_reserve_does_not_trigger_false_stall_alert(self, stall_setup) -> None:
        """环形缓冲尚有未播余量时不误报断流；余量耗尽后才按真停供告警"""
        m = stall_setup.manager
        m.start_stream(utterance_id="reserve")
        stream = stall_setup.sd.created_streams[-1]

        # 一次性突发推送 8s 音频（256000 帧 @ 32kHz），此后上游暂停发送
        m.write_chunk(np.zeros(256000, dtype=np.int16))
        m.logger.reset_mock()

        # 过 5.1s（回调尚未抽干缓冲，剩余 8.0s > 5.1s）→ 仍在正常放音，不告警
        stall_setup.now_holder[0] += 5.1
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

        # 模拟缓冲被声卡回调消费殆尽且又过 5.5s 无新块 → 真正停供断流，告警
        stream.pull(256000)
        stall_setup.now_holder[0] += 5.5
        assert m.check_playback_stall() is True
        m.logger.warning.assert_called_once()

        m.stop_stream(discard_pending=True)

    def test_eos_draining_does_not_trigger_false_stall_alert(self, stall_setup) -> None:
        """上游已声明结束进入排空期（长句余量慢慢播完）时，不误报断流"""
        m = stall_setup.manager
        m.start_stream(utterance_id="draining")
        stream = stall_setup.sd.created_streams[-1]
        m.write_chunk(np.zeros(32000, dtype=np.int16))

        stop_thread = threading.Thread(target=m.stop_stream, daemon=True)
        stop_thread.start()
        while stop_thread.is_alive() and not m.is_stream_draining:
            stop_thread.join(timeout=0.001)

        stall_setup.now_holder[0] += 10.0
        assert m.check_playback_stall() is False
        m.logger.warning.assert_not_called()

        while stream.active:
            stream.pull(16000)
            stop_thread.join(timeout=0.001)
        stop_thread.join(timeout=1.0)


class TestSinkFeedAtPlaybackCallback:
    """分接递入严格跟随回调播放游标：入队不递，回调实际消耗才由后台泵按序递出"""

    @pytest.fixture
    def tapped(self, sd) -> SimpleNamespace:
        sink = RecordingSink()
        manager = AudioDeviceManager(
            sample_rate=32000,
            sink=sink,
            prebuffer_seconds=0.05,
            stall_watchdog_enabled=False,
        )
        return SimpleNamespace(sink=sink, manager=manager, sd=sd)

    @staticmethod
    def _fed_total(sink: RecordingSink) -> int:
        return sum(len(chunk) for chunk in sink.fed_chunks())

    def test_arrival_does_not_feed_drain_feeds_at_playback(self, tapped) -> None:
        """预缓冲期到达不递入；收尾排空按回调消耗序完整递出"""
        tapped.manager.prebuffer_seconds = 100.0
        chunks = [np.full(1600, i, dtype=np.int16) for i in (1, 2, 3)]
        tapped.manager.start_stream(utterance_id="tap")
        stream = tapped.sd.created_streams[-1]
        for c in chunks:
            tapped.manager.write_chunk(c)
        tapped.manager.wait_until_sink_drained()
        assert self._fed_total(tapped.sink) == 0

        stop_and_drain(tapped.manager, stream, blocksize=1600)

        assert self._fed_total(tapped.sink) == 4800
        np.testing.assert_array_equal(np.concatenate(tapped.sink.fed_chunks()), np.concatenate(chunks))
        assert [c[0] for c in tapped.sink.calls][0] == "start"
        assert [c[0] for c in tapped.sink.calls][-1] == "stop"

    def test_stream_callbacks_carry_their_audio_to_the_sink(self, tapped) -> None:
        """开播后每次回调拉走多少样本分接才收到多少，收尾绝不重复递入"""
        chunk = np.arange(1600, dtype=np.int16)
        tapped.manager.start_stream(utterance_id="tap")
        stream = tapped.sd.created_streams[-1]
        for i in range(3):
            tapped.manager.write_chunk(chunk + i)

        # 未触发回调拉流前，已达阈值开播但尚无帧真正播出
        tapped.manager.wait_until_sink_drained()
        assert self._fed_total(tapped.sink) == 0

        stream.pull(1600)
        tapped.manager.wait_until_sink_drained()
        assert self._fed_total(tapped.sink) == 1600
        np.testing.assert_array_equal(tapped.sink.fed_chunks()[0], chunk)

        stop_and_drain(tapped.manager, stream, blocksize=1600)
        assert self._fed_total(tapped.sink) == 4800
        np.testing.assert_array_equal(
            np.concatenate(tapped.sink.fed_chunks()),
            np.concatenate([chunk + i for i in range(3)]),
        )

    def test_discard_leaves_sink_untouched(self, tapped) -> None:
        """取消/截断：未播的预缓冲被丢弃，不发声也不递分接"""
        tapped.manager.prebuffer_seconds = 100.0
        tapped.manager.start_stream(utterance_id="drop")
        stream = tapped.sd.created_streams[-1]
        tapped.manager.write_chunk(np.full(1600, 9, dtype=np.int16))

        tapped.manager.stop_stream(discard_pending=True)

        assert self._fed_total(tapped.sink) == 0
        assert stream.abort_count == 1
        assert [c[0] for c in tapped.sink.calls] == ["start", "stop"]
