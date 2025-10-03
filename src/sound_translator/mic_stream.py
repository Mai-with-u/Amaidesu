"""
麦克风采集并推送至 Fun-ASR 识别。
"""

from typing import Optional, Callable

try:
    import sounddevice as sd
    import numpy as np
except Exception:  # pragma: no cover
    sd = None  # type: ignore
    np = None  # type: ignore


class MicStreamer:
    def __init__(
        self,
        device_index: Optional[int],
        sample_rate: int = 16000,
        block_size: int = 3200,
        on_chunk: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        if sd is None or np is None:
            raise RuntimeError("sounddevice/numpy 不可用。请先安装依赖。")

        self.device_index = device_index
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.on_chunk = on_chunk
        self._stream: Optional[sd.InputStream] = None

    def _callback(self, indata, frames, time, status):  # type: ignore[override]
        if status:
            # 丢弃异常状态但继续
            pass
        if self.on_chunk is not None:
            # int16 PCM bytes
            pcm: bytes = (indata.copy().astype(np.int16)).tobytes()
            self.on_chunk(pcm)

    def start(self) -> None:
        self._stream = sd.InputStream(
            device=self.device_index,
            channels=1,
            samplerate=self.sample_rate,
            dtype="int16",
            blocksize=self.block_size,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


