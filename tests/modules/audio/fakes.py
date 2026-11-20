"""音频模块测试假对象与同步排空辅助。

全库对 ``AudioSink`` 协议和声卡回调流（``FakeCallbackOutputStream``）的测试替身
集中在此处：各测试文件从这里导入，禁止另造第二套。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple
from unittest.mock import MagicMock

import numpy as np

if TYPE_CHECKING:
    from src.modules.audio.audio_device_manager import AudioDeviceManager


class RecordingSink:
    """记录 start/feed/stop 调用的 ``AudioSink`` 桩；``boom=True`` 时全链路抛异常。"""

    def __init__(self, boom: bool = False) -> None:
        self.calls: List[Tuple[str, object]] = []
        self.boom = boom

    def start(self, utterance_id: str = "") -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("start", utterance_id))

    def feed(self, chunk: np.ndarray, sample_rate: int) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("feed", (chunk, sample_rate)))

    def stop(self) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("stop", None))

    def fed_chunks(self) -> List[np.ndarray]:
        """按序返回已递入的音频块。"""
        return [payload[0] for name, payload in self.calls if name == "feed"]


class FakeCallbackOutputStream:
    """模拟 PortAudio 回调模式 ``sounddevice.OutputStream`` 的可步进替身。

    测试主线程调用 :meth:`pull` 触发一次音频回调：事先将输出缓冲区填满非零脏值 ``77``，
    用来强验证真实回调是否显式用 PCM 或静音零值覆写了全部样本，不会靠初始化零值蒙混。
    """

    def __init__(
        self,
        samplerate: int = 32000,
        channels: int = 1,
        dtype: Any = np.int16,
        device: Any = None,
        callback: Optional[Callable[[np.ndarray, int, Any, Any], None]] = None,
        finished_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = np.dtype(dtype)
        self.device = device
        self.callback = callback
        self.finished_callback = finished_callback

        self.active = False
        self.method_calls: List[str] = []
        self.start_count = 0
        self.stop_count = 0
        self.abort_count = 0
        self.close_count = 0
        self.played_frames_chunks: List[np.ndarray] = []

    def start(self) -> None:
        self.method_calls.append("start")
        self.start_count += 1
        self.active = True

    def stop(self) -> None:
        self.method_calls.append("stop")
        self.stop_count += 1
        self.active = False

    def abort(self) -> None:
        self.method_calls.append("abort")
        self.abort_count += 1
        self.active = False

    def close(self) -> None:
        self.method_calls.append("close")
        self.close_count += 1
        self.active = False

    def pull(self, frames: int = 1600, status: Any = None) -> Tuple[np.ndarray, bool]:
        """模拟声卡中断回调一次拉取 ``frames`` 帧。

        Returns:
            ``(写出的数据块副本, 是否触发了 CallbackStop)``
        """
        if self.callback is None:
            raise AssertionError("FakeCallbackOutputStream 缺少 callback")
        # 故意先写满非零脏值，校验欠载/句尾补零必须显式清零输出缓冲
        out = np.full((frames, self.channels), 77, dtype=self.dtype)
        stopped = False
        try:
            self.callback(out, frames, None, status)
        except Exception as exc:
            if type(exc).__name__ not in ("CallbackStop", "_FallbackCallbackStop"):
                raise
            stopped = True
            self.active = False

        self.played_frames_chunks.append(out.copy())
        if stopped and self.finished_callback is not None:
            self.finished_callback()
        return out.copy(), stopped

    def played_audio(self) -> np.ndarray:
        """拼接所有回调输出的单声道/多声道样本序列（便于波形断言）。"""
        if not self.played_frames_chunks:
            return np.empty(0, dtype=self.dtype)
        merged = np.concatenate(self.played_frames_chunks, axis=0)
        return merged[:, 0] if self.channels == 1 else merged


def make_fake_sounddevice() -> MagicMock:
    """构造带回调 ``OutputStream`` 工厂的 ``sounddevice`` 模块替身。"""
    backend = MagicMock()
    created: List[FakeCallbackOutputStream] = []

    def _create_stream(**kwargs: Any) -> FakeCallbackOutputStream:
        stream = FakeCallbackOutputStream(**kwargs)
        created.append(stream)
        return stream

    backend.OutputStream = MagicMock(side_effect=_create_stream)
    backend.created_streams = created
    backend.default = SimpleNamespace(device=[0, 0])
    backend.query_devices = MagicMock(return_value=[])
    backend.play = MagicMock()
    backend.stop = MagicMock()
    return backend


def stop_and_drain(
    manager: AudioDeviceManager,
    stream: FakeCallbackOutputStream,
    *,
    blocksize: int = 1600,
    timeout: float = 2.0,
) -> None:
    """同步完成正常停流与声卡回调排空，不留后台等待超时。"""
    errors: List[BaseException] = []

    def _stop_worker() -> None:
        try:
            manager.stop_stream(discard_pending=False)
        except BaseException as exc:  # noqa: BLE001 - 收集子线程异常供主线程显式失败
            errors.append(exc)

    thread = threading.Thread(target=_stop_worker, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while thread.is_alive() and not (manager.is_stream_draining and stream.active):
        if time.monotonic() >= deadline:
            break
        thread.join(timeout=0.001)

    while stream.active:
        if time.monotonic() >= deadline:
            raise AssertionError("FakeCallbackOutputStream 排空超时未收到 CallbackStop")
        stream.pull(blocksize)
        thread.join(timeout=0.001)

    remaining = max(0.1, deadline - time.monotonic())
    thread.join(timeout=remaining)
    if thread.is_alive():
        raise AssertionError("stop_stream 在回调排空后仍未收敛退出")
    if errors:
        raise errors[0]


__all__ = [
    "FakeCallbackOutputStream",
    "RecordingSink",
    "make_fake_sounddevice",
    "stop_and_drain",
]
