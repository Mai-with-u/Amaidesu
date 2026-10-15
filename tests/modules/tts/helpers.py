"""TTS 引擎测试共享的替身与事件录制。"""

from __future__ import annotations

import base64
import struct
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.llm.payload import Response
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)

LIFECYCLE_EVENTS = (
    (CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload),
    (CoreEvents.TTS_UTTERANCE_FINISHED, UtteranceFinishedPayload),
    (CoreEvents.TTS_UTTERANCE_FAILED, UtteranceFailedPayload),
)


def record_lifecycle(bus: EventBus) -> list[tuple[str, object]]:
    """订阅 started / finished / failed 三类事件，按到达序记录 (事件名, 载荷)。"""
    captured: list[tuple[str, object]] = []

    async def capture(event_name: str, data: object, source: str) -> None:
        captured.append((event_name, data))

    for name, model in LIFECYCLE_EVENTS:
        bus.on(name, capture, model_class=model)
    return captured


def make_audio_manager() -> MagicMock:
    """声卡管理器替身：play_audio 会被 await 所以用 AsyncMock，流式三件套与 stop_audio 是同步方法。"""
    return MagicMock(play_audio=AsyncMock())


async def setup_with_audio_manager(provider, mgr: Optional[MagicMock] = None) -> MagicMock:
    """提前完成 setup 并把声卡管理器换成替身，handle_speech 的自治 ensure_setup 就不会再覆盖它。

    引擎内部用 ``from src.modules.audio import AudioDeviceManager`` 构造，所以拦截构造函数本身。
    """
    mgr = mgr or make_audio_manager()
    with patch("src.modules.audio.AudioDeviceManager", return_value=mgr):
        await provider.setup()
    return mgr


def make_wav_base64(sample_count: int = 2400) -> str:
    """最小合法 WAV（24kHz 单声道 int16 静音）的 base64 串。"""
    data = b"\x00\x00" * sample_count
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        24000,
        48000,
        2,
        16,
        b"data",
        len(data),
    )
    return base64.b64encode(header + data).decode("ascii")


def audio_response(
    *,
    audio_base64: Optional[str] = None,
    content: Optional[str] = None,
    success: bool = True,
    error: Optional[str] = None,
) -> Response:
    """全模态模型的音频应答；audio_base64 缺省给一段合法 WAV，传空串表示应答里没有音频。"""
    return Response(
        success=success,
        content=content,
        error=error,
        audio_base64=audio_base64 if audio_base64 is not None else make_wav_base64(),
        audio_format="wav",
    )


class FakeLLM:
    """AudioLLMInvoker 形状的记录替身。"""

    def __init__(self, response: Optional[Response] = None, error: Optional[Exception] = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_audio(self, input: Any, **kwargs: Any) -> Response:
        self.calls.append({"input": input, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def fake_http_session():
    """替换 Voicebox 的 aiohttp 会话：setup 的健康检查不打真实网络，会话保持未关闭状态。"""
    return patch(
        "src.modules.tts.backends.voicebox.provider.aiohttp.ClientSession", return_value=MagicMock(closed=False)
    )
