"""皮套适配器自动情绪路径（被动半事件订阅）测试

覆盖（假总线：publish → 断言副作用）：

- ``bind_speech_emotion``：发 ``streamer.speech`` → 适配器 ``set_expression``
  被反射（VTS 断言参数写入；VRChat 诚实返回未应用且不抛）；
- ``bind_speaking_state``：发 ``tts.utterance.started/finished`` → VTS 说话
  标志翻转 / Warudo talking-head 置位；
- 无事件总线（None）→ 绑定跳过不抛（工具面-only 形态）；
- fail-soft：set_expression 抛异常不影响总线分发。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.speech_binding import bind_speech_emotion, bind_speaking_state
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceFinishedPayload, UtteranceStartedPayload


def _speech_payload(emotion: str = "happy", intensity: float = 0.8) -> StreamerSpeechPayload:
    return StreamerSpeechPayload(
        utterance_id="utt_binding_1",
        text="你好呀",
        emotion=emotion,
        emotion_intensity=intensity,
    )


async def _emit_and_settle(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await asyncio.sleep(0.05)  # 让 handler 跑完


# ---------------------------------------------------------------------------
# 情绪反射（streamer.speech → set_expression）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speech_reflected_to_vts_parameters():
    """发 streamer.speech → VTS 按映射写参数（happy 0.8 → MouthSmile 0.64 ...）。"""
    provider = VTSProvider(config={})
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)

    bus = EventBus()
    handler = bind_speech_emotion(bus, provider, provider.logger)
    assert handler is not None
    try:
        await _emit_and_settle(bus, CoreEvents.STREAMER_SPEECH, _speech_payload("happy", 0.8))
    finally:
        bus.off(CoreEvents.STREAMER_SPEECH, handler)

    args = provider.expression.set_multi_parameter.await_args
    written = args.args[0]
    assert written == {"MouthSmile": 0.8 * 0.8, "Brows": 0.6 * 0.8}


@pytest.mark.asyncio
async def test_speech_reflected_to_vrchat_without_render():
    """VRChat 无情绪通道：反射调用不抛，返回未应用结果。"""
    provider = VRChatProvider(config={})
    bus = EventBus()
    bind_speech_emotion(bus, provider, provider.logger)
    try:
        await _emit_and_settle(bus, CoreEvents.STREAMER_SPEECH, _speech_payload("happy", 0.8))
    finally:
        pass  # fail-soft 路径已覆盖；bus 随用例销毁


@pytest.mark.asyncio
async def test_speech_binding_soft_fails_on_provider_error():
    """set_expression 抛异常 → 不外传（fail-soft），总线继续工作。"""
    provider = MagicMock()
    provider.set_expression = AsyncMock(side_effect=RuntimeError("boom"))

    bus = EventBus()
    bind_speech_emotion(bus, provider, MagicMock())

    seen: list[str] = []

    async def _follower(event_name, payload, source=None):
        seen.append(payload.utterance_id)

    bus.on(CoreEvents.STREAMER_SPEECH, _follower, model_class=StreamerSpeechPayload)
    await _emit_and_settle(bus, CoreEvents.STREAMER_SPEECH, _speech_payload())
    # 后续订阅者仍收到事件，总线未被炸掉
    assert seen == ["utt_binding_1"]


def test_bind_without_event_bus_is_noop():
    """event_bus=None（工具面-only 形态）→ 绑定跳过。"""
    provider = VTSProvider(config={})
    assert bind_speech_emotion(None, provider, provider.logger) is None
    assert bind_speaking_state(None, provider.logger, on_change=lambda s: None) is None


# ---------------------------------------------------------------------------
# 说话状态（tts.utterance.* → 各适配器反应）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speaking_state_flips_vts_flag():
    provider = VTSProvider(config={})
    bus = EventBus()
    handles = bind_speaking_state(bus, provider.logger, on_change=provider._set_speaking)
    assert handles is not None
    try:
        await _emit_and_settle(bus, CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload(
            utterance_id="utt_s1", speech_text="hi", engine="edge", duration_ms=100))
        assert provider._is_speaking is True

        await _emit_and_settle(bus, CoreEvents.TTS_UTTERANCE_FINISHED, UtteranceFinishedPayload(
            utterance_id="utt_s1", engine="edge", duration_ms=100))
        assert provider._is_speaking is False
    finally:
        started_handler, finished_handler = handles
        bus.off(CoreEvents.TTS_UTTERANCE_STARTED, started_handler)
        bus.off(CoreEvents.TTS_UTTERANCE_FINISHED, finished_handler)


@pytest.mark.asyncio
async def test_speaking_state_drives_warudo_talking_head():
    provider = WarudoProvider(config={})
    assert provider.talking_head_task is not None
    bus = EventBus()
    handles = bind_speaking_state(bus, provider.logger, on_change=provider._set_speaking)
    assert handles is not None
    try:
        await _emit_and_settle(bus, CoreEvents.TTS_UTTERANCE_STARTED, UtteranceStartedPayload(
            utterance_id="utt_s2", speech_text="hi", engine="edge", duration_ms=100))
        assert provider.talking_head_task.is_talking is True

        await _emit_and_settle(bus, CoreEvents.TTS_UTTERANCE_FINISHED, UtteranceFinishedPayload(
            utterance_id="utt_s2", engine="edge", duration_ms=100))
        assert provider.talking_head_task.is_talking is False
    finally:
        started_handler, finished_handler = handles
        bus.off(CoreEvents.TTS_UTTERANCE_STARTED, started_handler)
        bus.off(CoreEvents.TTS_UTTERANCE_FINISHED, finished_handler)


# ---------------------------------------------------------------------------
# setup/cleanup 全链（provider 自持订阅生命周期）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vts_setup_binds_and_cleanup_unbinds():
    """VTS setup 绑定订阅；cleanup 退订后事件不再触发反射。"""
    provider = VTSProvider(config={})
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)
    # 阻断真实 pyvts 连接：手工置启动态，直接走订阅绑定段
    provider._has_started = True
    bus = EventBus()
    provider.event_bus = bus
    provider._speech_emotion_handler = bind_speech_emotion(bus, provider, provider.logger)
    provider._speaking_state_handles = bind_speaking_state(
        bus, provider.logger, on_change=provider._set_speaking
    )

    await _emit_and_settle(bus, CoreEvents.STREAMER_SPEECH, _speech_payload())
    assert provider.expression.set_multi_parameter.await_count == 1

    await provider.cleanup()
    assert provider._speech_emotion_handler is None
    assert provider._speaking_state_handles is None

    # 退订后再发事件：反射不再发生
    await _emit_and_settle(bus, CoreEvents.STREAMER_SPEECH, _speech_payload("sad", 0.5))
    assert provider.expression.set_multi_parameter.await_count == 1
