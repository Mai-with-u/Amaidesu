"""OmniTTSProvider（全模态大模型合成）特有行为：构造与配置校验、LLM 调用参数、PCM 播放、取消截断、转写偏差告警

四个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.tts.backends.omni.provider import OmniTTSProvider
from tests.modules.tts.helpers import FakeLLM, audio_response, make_audio_manager, setup_with_audio_manager


def _provider(
    llm: Optional[FakeLLM] = None, bus: Optional[EventBus] = None, **config
) -> tuple[OmniTTSProvider, FakeLLM]:
    fake = llm or FakeLLM(audio_response())
    provider = OmniTTSProvider(
        config={"type": "omni_tts", "voice": "Chelsie", **config}, event_bus=bus, llm_service=fake
    )
    return provider, fake


def test_construction_requires_llm_service() -> None:
    with pytest.raises(RuntimeError, match="llm_service"):  # 装配 fail-soft 的触发面
        OmniTTSProvider(config={"type": "omni_tts", "voice": "Chelsie"})


@pytest.mark.parametrize(
    ("config", "error", "match"),
    [({"llm_profile": "totally_unknown"}, ValueError, "totally_unknown"), ({"voice": ""}, RuntimeError, "voice")],
)
async def test_setup_rejects_unknown_profile_and_missing_voice(config: dict, error: type, match: str) -> None:
    provider, _ = _provider(**config)
    with pytest.raises(error, match=match):
        await provider.setup()


async def test_llm_called_with_configured_params_and_pcm_played_at_wav_samplerate() -> None:
    provider, fake = _provider(llm_profile="tts", style_prompt="语气轻快")
    mgr = await setup_with_audio_manager(provider)

    await provider.handle_speech("你好，欢迎来到直播间", utterance_id="utt_1")

    (call,) = fake.calls
    assert (call["input"], call["profile"], call["voice"], call["audio_format"], call["system"]) == (
        "你好，欢迎来到直播间",
        "tts",
        "Chelsie",
        "wav",
        "语气轻快",
    )
    args, kwargs = mgr.play_audio.call_args
    pcm = args[0] if args else kwargs["audio_array"]
    assert isinstance(pcm, np.ndarray) and pcm.dtype == np.int16 and len(pcm) == 2400
    assert kwargs.get("samplerate") == 24000
    stats = provider.get_stats()
    assert (stats["render_count"], stats["error_count"], stats["is_connected"]) == (1, 0, True)
    assert (stats["llm_profile"], stats["voice"]) == ("tts", "Chelsie")


async def test_empty_style_prompt_passes_none_system_and_blank_text_skips_render() -> None:
    provider, fake = _provider()
    await setup_with_audio_manager(provider)
    await provider.handle_speech("x")
    assert fake.calls[0]["system"] is None

    await provider.handle_speech("   ")  # 规范化后仍为空，不触达 LLM
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("llm", "match"),
    [
        (FakeLLM(audio_response(success=False, error="boom")), "boom"),
        (FakeLLM(audio_response(audio_base64="")), "未携带音频"),
    ],
)
async def test_unsuccessful_or_audioless_reply_raises_without_touching_the_sound_card(llm: FakeLLM, match: str) -> None:
    provider, _ = _provider(llm)
    mgr = await setup_with_audio_manager(provider)
    with pytest.raises(RuntimeError, match=match):
        await provider.handle_speech("你好", utterance_id="utt_fail")
    mgr.stop_audio.assert_not_called()  # 合成阶段失败未触达声卡，无需截断


async def test_cancellation_truncates_submitted_playback() -> None:
    provider, _ = _provider()
    mgr = make_audio_manager()
    mgr.play_audio = AsyncMock(side_effect=asyncio.CancelledError())
    await setup_with_audio_manager(provider, mgr)
    with pytest.raises(asyncio.CancelledError):
        await provider.handle_speech("你好", utterance_id="utt_cancel")
    mgr.stop_audio.assert_called()  # 取消不会停下已提交声卡的播放，必须显式截断


@pytest.mark.parametrize(("transcript", "warned"), [("你好，欢迎来到直播间！", True), ("你好，欢迎来到直播间", False)])
async def test_rewritten_transcript_warns_but_still_plays(transcript: str, warned: bool, loguru_capture) -> None:
    provider, _ = _provider(FakeLLM(audio_response(content=transcript)))
    mgr = await setup_with_audio_manager(provider)
    await provider.handle_speech("你好，欢迎来到直播间")
    # 模型改写台词的偏差要可见，但不阻断播放
    assert any("transcript 与输入台词不一致" in r["message"] for r in loguru_capture.records) is warned
    mgr.play_audio.assert_called_once()
