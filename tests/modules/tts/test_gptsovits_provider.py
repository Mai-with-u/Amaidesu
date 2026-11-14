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
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts.backends.gptsovits.client import GPTSoVITSServiceError
from src.modules.tts.backends.gptsovits.provider import DTYPE, GPTSoVITSProvider
from src.modules.events.payloads.utterance import is_failure_reported
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


def test_get_stats_reports_debug_params_with_config_defaults() -> None:
    """调试参数描述表逐项含键名/标签/值域/步长/小数位，默认值取引擎当前生效配置。"""
    provider = GPTSoVITSProvider(
        config={
            "type": "gptsovits",
            "temperature": 0.7,
            "speed_factor": 1.2,
            "sample_steps": 16,
            "top_k": 30,
            "top_p": 0.8,
        }
    )
    params = {p["key"]: p for p in provider.get_stats()["debug_params"]}
    assert set(params) == {"temperature", "speed_factor", "sample_steps", "top_k", "top_p"}
    temperature = params["temperature"]
    assert temperature["label"] == "温度"
    assert (temperature["min"], temperature["max"], temperature["step"], temperature["precision"]) == (0.0, 2.0, 0.1, 1)
    assert temperature["default"] == 0.7
    assert params["speed_factor"]["default"] == 1.2 and params["speed_factor"]["label"] == "语速"
    assert params["sample_steps"]["default"] == 16 and params["sample_steps"]["precision"] == 0
    assert params["top_k"]["default"] == 30
    assert params["top_p"]["default"] == 0.8 and params["top_p"]["precision"] == 2


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
    mgr.open_output_stream.side_effect = lambda: log.append("open") or "声卡流"
    mgr.start_prepared_stream.side_effect = lambda stream, utterance_id="": log.append(f"start:{stream}:{utterance_id}")
    mgr.write_chunk.side_effect = lambda chunk: log.append(f"write:{len(chunk)}")
    mgr.stop_stream.side_effect = lambda: log.append("stop")
    await _started(provider, _stream_client(b"c1", b"c2", b"c3"), mgr)

    async def decode(chunk: bytes, dtype: Any = None) -> np.ndarray:
        return np.arange(2048, dtype=DTYPE)

    with patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=decode):
        await provider.handle_speech("x", utterance_id="utt_order")

    # 先打开声卡（工作线程里），再用打开的流开始这一句，然后逐块写、收尾
    assert log == ["open", "start:声卡流:utt_order", "write:2048", "write:2048", "write:2048", "stop"]


async def test_finished_duration_ms_computed_from_pcm_samples(event_bus: EventBus) -> None:
    """finished 的 duration_ms = 写往声卡的 PCM 样本数 ÷ 采样率（事件契约要求）。"""
    provider = GPTSoVITSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client(b"\x00\x00" * 1600, b"\x00\x00" * 1600))
    # 裸 PCM 块（无 RIFF 头）按原始 int16 解码：两块各 1600 样本，共 3200 样本 @ 32kHz = 100ms
    await provider.handle_speech("你好", utterance_id="utt_duration")
    await event_bus.cleanup()

    (started, finished) = [p for name, p in captured if name != CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(started, UtteranceStartedPayload) and started.duration_ms is None
    assert isinstance(finished, UtteranceFinishedPayload) and finished.duration_ms == 100


async def test_cancellation_mid_stream_emits_no_finished(event_bus: EventBus) -> None:
    """中途取消（队列看门狗超时）不发 finished：未播完的发声没有播放完成时刻。"""
    provider = GPTSoVITSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, _stream_client(b"c1", b"c2"))
    mgr.write_chunk.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider.handle_speech("你好", utterance_id="utt_cancel")
    await event_bus.cleanup()

    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED]


async def test_missing_reference_audio_fails_before_synthesis(event_bus: EventBus) -> None:
    provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client())
    provider.ref_audio_path = provider.prompt_text = ""  # 模拟配置缺失

    with pytest.raises(RuntimeError, match="参考音频未配置") as raised:
        await provider.handle_speech("你好", utterance_id="utt_no_ref")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload) and "ref_audio_path" in failed.error_message
    # 通用失败分支同样先发 failed 再抛：异常带"已报告"标记，队列兜底不重复补发
    assert is_failure_reported(raised.value)


async def test_cancellation_while_waiting_for_lock_keeps_cancelled_error() -> None:
    """等锁期取消（如调试试播占锁时看门狗超时）原样抛 CancelledError。

    回归锚：write_task 曾在锁内才初始化，取消落在等锁 / 发请求 / 开声卡
    阶段时取消分支引用未绑定名字，CancelledError 被替换成 UnboundLocalError，
    编排队列的取消语义随之错乱。
    """
    provider = GPTSoVITSProvider(config=dict(CONFIGURED))
    mgr = await _started(provider, _stream_client())
    await provider.tts_lock.acquire()  # 模拟调试面板试播占锁
    try:
        task = asyncio.create_task(provider.handle_speech("你好", utterance_id="utt_wait_cancel"))
        for _ in range(20):
            await asyncio.sleep(0)  # 让任务推进到等锁挂起点（零真实时间，纯调度让出）
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        provider.tts_lock.release()
    mgr.stop_stream.assert_called_with(discard_pending=True)  # 取消收尾照常执行


async def test_mid_stream_failure_closes_audio_stream_and_discards_pending(event_bus: EventBus) -> None:
    """声卡已开后流式迭代中途失败：关流并丢弃预缓冲（资源泄漏回归锚）。

    回归锚：失败分支曾不关流——声卡句柄泄漏、口型分接会话残留、断流巡检
    以"播放中断流"误报合成失败。stop_stream(discard_pending=True) 使失败
    半句不冲播，与取消语义一致。
    """
    provider = GPTSoVITSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = make_audio_manager()

    def _dying_stream(**_kwargs: Any) -> Any:
        def _gen() -> Any:
            yield b"c1"
            raise RuntimeError("stream died mid-iteration")

        return _gen()

    client = MagicMock(tts_stream=MagicMock(side_effect=_dying_stream))
    await _started(provider, client, mgr)

    async def decode(chunk: bytes, dtype: Any = None) -> np.ndarray:
        return np.arange(1024, dtype=DTYPE)

    with patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=decode):
        with pytest.raises(RuntimeError, match="stream died"):
            await provider.handle_speech("你好", utterance_id="utt_mid_fail")
    await event_bus.cleanup()

    mgr.stop_stream.assert_called_with(discard_pending=True)
    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload) and "stream died" in failed.error_message


async def test_service_unreachable_reports_short_message_without_transport_noise(event_bus: EventBus) -> None:
    provider = GPTSoVITSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    error = GPTSoVITSServiceError("GPT-SoVITS 服务不可达 (http://127.0.0.1:9880)，请确认服务已启动")
    await _started(provider, _stream_client(error=error))

    with pytest.raises(GPTSoVITSServiceError, match="服务不可达") as raised:
        await provider.handle_speech("你好", utterance_id="utt_svc_down")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert "服务不可达" in failed.error_message and "HTTPConnectionPool" not in failed.error_message
    # 服务环境故障分支也先发 failed 再抛：异常带"已报告"标记，队列兜底不重复补发
    assert is_failure_reported(raised.value)


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
