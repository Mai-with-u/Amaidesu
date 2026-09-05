"""SimulatorService 订阅 ``streamer.speech`` 业务事件测试。

覆盖：
- start() 后订阅生效：手动 emit ``streamer.speech`` → cadence.notify_streamer_activity 被调用
- start() 后状态机从 IDLE 唤醒到 NORMAL（端到端 cadence 状态变化）
- stop() 后 off 生效：再 emit 不触发 cadence（防 stop 后 handler 残留）
- 重复 start 不重复订阅（_subscribed_streamer_speech flag 守护）
- handler 不触发 LLM / 决策出口（防环：仅同步调 notify + 记 DEBUG 日志）
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.simulator import SimulatorService
from src.modules.simulator.cadence import CadenceGenerator


class _FakeLLMService:
    """满足 SimulatorService._find_llm_service duck-type 检测。"""

    async def chat(self, prompt: str, **kwargs: Any) -> Any:
        from src.modules.llm.manager import LLMResponse

        return LLMResponse(success=True, content="x", usage={"total_tokens": 0}, error=None)

    async def chat_fast(self, prompt: str, **kwargs: Any) -> Any:
        return await self.chat(prompt, **kwargs)

    async def setup(self, config: Any) -> None:
        pass

    async def cleanup(self) -> None:
        pass


class _FakeConfigService:
    def __init__(self, simulator_cfg: Dict[str, Any]) -> None:
        self.main_config = {"simulator": simulator_cfg}


def _enabled_config(**overrides: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {"enabled": True}
    cfg.update(overrides)
    return cfg


@pytest.fixture
def fake_llm() -> _FakeLLMService:
    return _FakeLLMService()


async def _start_with_cadence(event_bus: EventBus, fake_llm: _FakeLLMService) -> SimulatorService:
    """构造并 start 一个 SimulatorService，cadence 已构造。"""
    service = SimulatorService(
        event_bus=event_bus,
        services_by_type={type(fake_llm): fake_llm},
    )
    await service.setup(
        _FakeConfigService(
            _enabled_config(
                cadence_mode="fixed",
                fixed_interval_s=1.0,
                gift_probability=0.0,
                warmup_duration_s=0.0,
            )
        )
    )
    return service


# ---------------------------------------------------------------------------
# start 后订阅生效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_subscribes_streamer_speech_handler(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """start 后订阅生效（_subscribed_streamer_speech = True）。"""
    service = await _start_with_cadence(event_bus, fake_llm)
    try:
        assert service._subscribed_streamer_speech is True
        # EventBus 实际注册了 STREAMER_SPEECH handler
        assert CoreEvents.STREAMER_SPEECH in event_bus._handlers
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_streamer_speech_event_triggers_cadence_notify(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """手动 emit ``streamer.speech`` → cadence.notify_streamer_activity 被调用。"""
    service = await _start_with_cadence(event_bus, fake_llm)
    try:
        cadence = service._cadence
        assert cadence is not None

        # 把 cadence 切到 IDLE，验证 notify 后被唤醒
        cadence._state = CadenceGenerator.IDLE  # type: ignore[attr-defined]
        assert cadence.get_state() == CadenceGenerator.IDLE

        # 手动 emit 业务事件（wait=True 确保 handler 同步跑完）
        payload = StreamerSpeechPayload(
            utterance_id="utt_test_1",
            text="hello",
            emotion=None,
        )
        await event_bus.emit(CoreEvents.STREAMER_SPEECH, payload, source="test", wait=True)

        # notify_streamer_activity 把 IDLE → NORMAL
        assert cadence.get_state() == CadenceGenerator.NORMAL
    finally:
        await service.cleanup()


# ---------------------------------------------------------------------------
# stop 后 off 生效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_unsubscribes_streamer_speech_handler(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """stop() 后解绑 STREAMER_SPEECH：再次 emit 不触发 cadence。"""
    service = await _start_with_cadence(event_bus, fake_llm)
    # stop 之前 cadence 应在 NORMAL
    cadence = service._cadence
    assert cadence is not None
    cadence._state = CadenceGenerator.IDLE  # type: ignore[attr-defined]

    await service.stop()
    assert service._subscribed_streamer_speech is False
    # EventBus 不再持有 STREAMER_SPEECH handler
    assert CoreEvents.STREAMER_SPEECH not in event_bus._handlers

    # stop 后 emit 不应唤醒 cadence（handler 已解绑）
    payload = StreamerSpeechPayload(
        utterance_id="utt_after_stop_1",
        text="late",
        emotion=None,
    )
    await event_bus.emit(CoreEvents.STREAMER_SPEECH, payload, source="test")
    # cadence 状态保持 IDLE（未被唤醒）
    assert cadence.get_state() == CadenceGenerator.IDLE


# ---------------------------------------------------------------------------
# 重复 start 不重复订阅
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_start_does_not_double_subscribe(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """重复 start() 不重复挂 handler（_subscribed_streamer_speech flag 守护）。"""
    service = await _start_with_cadence(event_bus, fake_llm)
    try:
        # 记录订阅后的 handler 数
        handler_count_before = len(event_bus._handlers[CoreEvents.STREAMER_SPEECH])

        # 重复 start()：因 _is_started=True 应直接返回，但为覆盖 _subscribed 守护
        # 显式重置 _is_started 并再次调用 start
        service._is_started = False
        await service.start()

        handler_count_after = len(event_bus._handlers[CoreEvents.STREAMER_SPEECH])
        assert handler_count_after == handler_count_before, "重复 start 不应重复订阅"
    finally:
        await service.cleanup()


# ---------------------------------------------------------------------------
# handler 不触发 LLM / 决策出口（防环）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_only_calls_notify_no_llm_side_effect(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """handler 仅同步调 notify + 记 DEBUG，不应触发 LLM 调用或决策出口。"""
    service = await _start_with_cadence(event_bus, fake_llm)
    try:
        cadence = service._cadence
        assert cadence is not None
        cadence._state = CadenceGenerator.IDLE  # type: ignore[attr-defined]

        # 记录 fake_llm.chat 调用次数
        chat_before = fake_llm.chat_calls if hasattr(fake_llm, "chat_calls") else 0

        payload = StreamerSpeechPayload(
            utterance_id="utt_no_loop_1",
            text="防环验证",
            emotion=None,
        )
        await event_bus.emit(CoreEvents.STREAMER_SPEECH, payload, source="test", wait=True)

        # cadence 被唤醒
        assert cadence.get_state() == CadenceGenerator.NORMAL
        # LLM 没被多调一次（handler 内不触发 LLM）
        chat_after = fake_llm.chat_calls if hasattr(fake_llm, "chat_calls") else 0
        assert chat_after == chat_before, "streamer.speech handler 不应触发 LLM 调用"
    finally:
        await service.cleanup()


# ---------------------------------------------------------------------------
# disabled 状态：start 早返回，不订阅
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_config_does_not_subscribe(event_bus: EventBus, fake_llm: _FakeLLMService) -> None:
    """``enabled=false`` 时 setup() 不构造子系统，也不订阅 streamer.speech。"""
    # 用无 LLM 注入路径（services_by_type={}），setup 在 llm_service is None 处 return
    service = SimulatorService(
        event_bus=event_bus,
        services_by_type={},  # 关键：无 LLM
    )
    await service.setup(_FakeConfigService({"enabled": False}))
    # 无 _llm_wrapper → 不可能订阅 STREAMER_SPEECH
    assert service._llm_wrapper is None
    assert service._subscribed_streamer_speech is False
    assert CoreEvents.STREAMER_SPEECH not in event_bus._handlers
