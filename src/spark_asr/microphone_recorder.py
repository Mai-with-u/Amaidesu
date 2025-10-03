# -*- encoding:utf-8 -*-
"""
轻量麦克风封装，最小侵入式替换原有 sd.InputStream 直接使用。

用法：
    recorder = MicrophoneRecorder(
        samplerate=16000,
        channels=1,
        dtype="int16",
        blocksize=640,
        device=None,
        callback=your_callback,
    )
    recorder.start()
    # ...
    recorder.stop()
"""

from __future__ import annotations

from typing import Callable, Optional

try:
    import sounddevice as sd
except Exception:  # 容错：由调用方在缺库时给出提示
    sd = None  # type: ignore


class MicrophoneRecorder:
    def __init__(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        blocksize: int,
        device: Optional[int | str],
        callback: Callable[[object, int, object, object], None],
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize
        self.device = device
        self._callback = callback
        self._stream = None

    def start(self) -> bool:
        if getattr(self, "_stream", None) is not None:
            return True
        if sd is None:
            return False
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.blocksize,
                callback=self._callback,
                device=self.device,
            )
            self._stream.start()
            return True
        except Exception:
            # 由上层打印具体错误信息
            self._stream = None
            return False

    def stop(self) -> None:
        if getattr(self, "_stream", None) is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None


