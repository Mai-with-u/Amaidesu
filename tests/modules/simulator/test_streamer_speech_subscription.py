"""SimulatorService 订阅 streamer.speech：主播发言唤醒节奏状态机，stop 后解绑，重复 start 不重复订阅，handler 不触发 LLM"""

from __future__ import annotations

import asyncio

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.simulator import SimulatorService
from src.modules.simulator.cadence import CadenceGenerator
from src.modules.storage.database import SQLiteDatabase
from tests.modules.simulator.helpers import FakeConfigService, FakeLLMService, enabled_config


def _speech(utterance_id: str = "utt_1") -> StreamerSpeechPayload:
    return StreamerSpeechPayload(utterance_id=utterance_id, text="hello", emotion="neutral", emotion_intensity=0.5)


@pytest.fixture
async def started(event_bus: EventBus, store: SQLiteDatabase):
    """已 setup 的服务（fixed 节奏、关礼物）与其假 LLM。"""
    llm = FakeLLMService()
    service = SimulatorService(
        event_bus=event_bus, sim_repo=store.sim, chat_repo=store.chat, services_by_type={type(llm): llm}
    )
    await service.setup(
        enabled_config(cadence_mode="fixed", fixed_interval_s=1.0, gift_probability=0.0, warmup_duration_s=0.0)
    )
    yield service, llm
    await service.cleanup()


async def test_speech_event_wakes_idle_cadence_without_touching_llm(event_bus: EventBus, started) -> None:
    service, llm = started
    assert service._subscribed_streamer_speech is True and CoreEvents.STREAMER_SPEECH in event_bus._handlers
    cadence = service._cadence
    assert cadence is not None
    cadence._state = CadenceGenerator.IDLE  # type: ignore[attr-defined]
    calls_before = llm.chat_calls

    await event_bus.emit(CoreEvents.STREAMER_SPEECH, _speech(), source="test")
    await asyncio.sleep(0.05)

    assert cadence.get_state() == CadenceGenerator.NORMAL  # notify_streamer_activity 把 IDLE 唤醒到 NORMAL
    assert llm.chat_calls == calls_before  # 防环：handler 只同步 notify，不触发 LLM 与决策出口


async def test_repeated_start_does_not_duplicate_subscription(event_bus: EventBus, started) -> None:
    service, _ = started
    before = len(event_bus._handlers[CoreEvents.STREAMER_SPEECH])
    service._is_started = False  # 绕过已启动早返回，直接覆盖 _subscribed 守护
    await service.start()
    assert len(event_bus._handlers[CoreEvents.STREAMER_SPEECH]) == before


async def test_stop_unsubscribes_so_later_speech_no_longer_wakes_cadence(event_bus: EventBus, started) -> None:
    service, _ = started
    cadence = service._cadence
    assert cadence is not None
    cadence._state = CadenceGenerator.IDLE  # type: ignore[attr-defined]

    await service.stop()
    assert service._subscribed_streamer_speech is False and CoreEvents.STREAMER_SPEECH not in event_bus._handlers

    await event_bus.emit(CoreEvents.STREAMER_SPEECH, _speech("utt_after_stop"), source="test")
    assert cadence.get_state() == CadenceGenerator.IDLE  # stop 后残留的 handler 不能再唤醒


async def test_disabled_config_does_not_subscribe(event_bus: EventBus) -> None:
    service = SimulatorService(event_bus=event_bus, services_by_type={})
    await service.setup(FakeConfigService({"enabled": False}))
    assert service._llm_wrapper is None and service._subscribed_streamer_speech is False
    assert CoreEvents.STREAMER_SPEECH not in event_bus._handlers
