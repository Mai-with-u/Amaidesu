"""GPTSoVITSProvider 特有行为：流式播放顺序、参考音频必填、服务不可达的简短报错、启动预热、整段合成

四个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceFailedPayload
from src.modules.tts.backends.gptsovits.client import GPTSoVITSServiceError
from src.modules.tts.backends.gptsovits.provider import DTYPE, GPTSoVITSProvider
from tests.modules.tts.helpers import make_audio_manager, record_lifecycle, setup_with_audio_manager

# api_v2 每请求必填参考音频：走合成路径的测试统一用已配置参考音频的引擎
CONFIGURED = {"type": "gptsovits", "ref_audio_path": "referenceAudio/ref.wav", "prompt_text": "参考转写"}


async def _started(provider: GPTSoVITSProvider, client: MagicMock, mgr: MagicMock | None = None) -> MagicMock:
    """先 setup 再换上替身客户端，并等启动预热任务收尾（替身下瞬时完成），避免测试 loop 关闭时残留任务。"""
    mgr = await setup_with_audio_manager(provider, mgr or make_audio_manager())
    provider.tts_client = client
    if provider._warmup_task is not None:
        await asyncio.gather(provider._warmup_task, return_exceptions=True)
    return mgr


def _stream_client(*chunks: bytes, error: Exception | None = None) -> MagicMock:
    return MagicMock(tts_stream=MagicMock(side_effect=error) if error else MagicMock(return_value=iter(chunks)))


def test_schema_rejects_mp3_and_backend_is_api_v2() -> None:
    with pytest.raises(ValidationError):  # api_v2 不支持 mp3，值域收紧为 wav/ogg
        GPTSoVITSProvider.ConfigSchema.from_dict({"type": "gptsovits", "media_type": "mp3"})
    assert GPTSoVITSProvider(config={"type": "gptsovits"}).get_stats()["backend"] == "api_v2"


async def test_handle_speech_with_empty_stream_counts_one_render() -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED))
    assert provider._has_started is False
    await _started(provider, _stream_client())
    await provider.handle_speech("你好")
    assert provider._has_started is True and provider.render_count == 1


async def test_stream_lifecycle_start_then_writes_then_stop() -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED))
    mgr = make_audio_manager()
    log: list[str] = []
    mgr.start_stream.side_effect = lambda utterance_id="": log.append(f"start:{utterance_id}")
    mgr.write_chunk.side_effect = lambda chunk: log.append(f"write:{len(chunk)}")
    mgr.stop_stream.side_effect = lambda: log.append("stop")
    await _started(provider, _stream_client(b"c1", b"c2", b"c3"), mgr)

    async def decode(chunk: bytes, dtype: Any = None) -> np.ndarray:
        return np.arange(2048, dtype=DTYPE)

    with patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=decode):
        await provider.handle_speech("x", utterance_id="utt_order")

    assert log == ["start:utt_order", "write:2048", "write:2048", "write:2048", "stop"]


async def test_missing_reference_audio_fails_before_synthesis(event_bus: EventBus) -> None:
    provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client())
    provider.ref_audio_path = provider.prompt_text = ""  # 模拟配置缺失

    with pytest.raises(RuntimeError, match="参考音频未配置"):
        await provider.handle_speech("你好", utterance_id="utt_no_ref")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload) and "ref_audio_path" in failed.error_message


async def test_service_unreachable_reports_short_message_without_transport_noise(event_bus: EventBus) -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    error = GPTSoVITSServiceError("GPT-SoVITS 服务不可达 (http://127.0.0.1:9880)，请确认服务已启动")
    await _started(provider, _stream_client(error=error))

    with pytest.raises(GPTSoVITSServiceError, match="服务不可达"):
        await provider.handle_speech("你好", utterance_id="utt_svc_down")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert "服务不可达" in failed.error_message and "HTTPConnectionPool" not in failed.error_message


async def test_startup_warmup_primes_server_and_failure_is_swallowed() -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED))
    client = MagicMock()
    await _started(provider, client)
    # 参考音频预处理 + 一次短文本合成：把服务端冷启动惩罚挪出直播首句
    client.set_refer_audio_remote.assert_called_once_with("referenceAudio/ref.wav")
    client.tts.assert_called_once()

    broken = GPTSoVITSProvider(config=dict(CONFIGURED))
    await _started(broken, MagicMock(tts=MagicMock(side_effect=RuntimeError("connection refused"))))
    assert broken._warmup_task.done()  # 预热失败只告警，不向装配方传播


async def test_synthesize_bytes_passes_provider_params_and_guards_inputs() -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED))
    with pytest.raises(RuntimeError, match="setup"):  # 未 setup 直接报错指引初始化
        provider.synthesize_bytes("你好")

    client = MagicMock()
    await _started(provider, client)
    client.reset_mock()
    client.tts.return_value = b"RIFF....wav"

    assert provider.synthesize_bytes("你好🎉世界") == b"RIFF....wav"
    kwargs = client.tts.call_args.kwargs
    assert kwargs["text"] == "你好，世界"  # emoji 已映射为停顿
    assert (kwargs["streaming_mode"], kwargs["media_type"]) == (False, "wav")
    assert (kwargs["temperature"], kwargs["repetition_penalty"], kwargs["sample_steps"]) == (1.0, 1.35, 10)

    client.reset_mock()
    assert provider.synthesize_bytes("   ") == b""  # 规范化后为空：不调客户端
    client.tts.assert_not_called()
