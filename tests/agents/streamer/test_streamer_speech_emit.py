"""StreamerAgent 业务事件 ``streamer.speech`` 接线测试。

覆盖需求：
- speech 非空 + TTS 关闭：仍 emit ``streamer.speech``（业务事实与 TTS 启用正交）
- speech 非空 + TTS 启用：emit ``streamer.speech`` 且 utterance_id 与 TTS 队列复用同一 id
- speech 空 / 仅空白：不 emit（业务事实不存在）
- speech 非空 + context_service 注入：ContextService.add_message 收到 ASSISTANT 消息
- 缺 context_service：跳过历史写入，不抛异常

测试方法：用真 EventBus 订阅 ``streamer.speech``，在 ``_dispatch_speech_and_emotion``
后等待 fire-and-forget 任务完成（``asyncio.sleep``），验证收到的事件 payload。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.context.models import MessageRole
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload


def _make_agent_config(**overrides: Any) -> StreamerAgentConfig:
    """构造测试用 StreamerAgentConfig。"""
    defaults: Dict[str, Any] = {
        "planner_llm": "llm_fast",
        "replyer_llm": "llm",
        "proactive_enabled": False,
        "agenda_enabled": False,
        "profanity_enabled": False,
        "batch_window_ms": 100,
        "tick_interval_ms": 50,
    }
    defaults.update(overrides)
    return StreamerAgentConfig(**defaults)


def _build_streamer_agent_with_bus(
    *,
    event_bus: Optional[EventBus] = None,
    speech_config: Optional[Dict[str, Any]] = None,
    tts_engine: Optional[Any] = None,
    context_service: Optional[Any] = None,
) -> StreamerAgent:
    """构造最小化 StreamerAgent：注入真 EventBus + 可选 TTS / context。"""
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(success=False, content=""))
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    if context_service is None:
        ctx = MagicMock()
        ctx.get_history = AsyncMock(return_value=[])
        ctx.add_message = AsyncMock(return_value=None)
        context_service = ctx

    config = _make_agent_config()
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context_service,
        event_bus=event_bus,
        tool_registry=None,
        speech_config=speech_config,
        tts_engine=tts_engine,
    )


class _MockTTSEngine:
    """TTS 引擎 mock：录制所有 handle_speech 调用。"""

    def __init__(self) -> None:
        self.handle_speech_calls: List[tuple[str, Optional[str]]] = []

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.handle_speech_calls.append((text, utterance_id))


async def _wait_for_speech_event(event_bus: EventBus, timeout: float = 2.0) -> Optional[StreamerSpeechPayload]:
    """辅助：等待 fire-and-forget 任务把 STREAMER_SPEECH 推出去。"""
    received: List[StreamerSpeechPayload] = []
    event = asyncio.Event()

    async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
        if isinstance(payload, StreamerSpeechPayload):
            received.append(payload)
            event.set()

    event_bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    return received[0] if received else None


# ---------------------------------------------------------------------------
# TTS 关闭仍 emit 业务事件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamer_speech_emitted_when_tts_disabled():
    """speech 非空 + TTS 关闭 → 仍 emit ``streamer.speech``（业务事实与 TTS 无关）。"""
    bus = EventBus()
    agent = _build_streamer_agent_with_bus(
        event_bus=bus,
        tts_engine=None,
        speech_config={"enabled": False},
    )
    await agent._on_start()
    try:
        payload_dict = {"speech": "今天好冷", "emotion": "", "action": "", "metadata": {}}
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        captured = await _wait_for_speech_event(bus)
        assert captured is not None, "TTS 关闭时仍应收到 streamer.speech"
        assert captured.text == "今天好冷"
        assert captured.emotion is None
        assert captured.utterance_id.startswith("utt_")
        # TTS 关闭时 _utterance_queue 仍为 None
        assert agent._utterance_queue is None
        # seq 已自增
        assert agent._utterance_seq == 1
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_streamer_speech_emitted_when_tts_enabled_and_no_engine():
    """speech_config.enabled=True 但 tts_engine=None → 降级关闭，仍 emit。"""
    bus = EventBus()
    agent = _build_streamer_agent_with_bus(
        event_bus=bus,
        tts_engine=None,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        # 启动期应已检测到缺失并降级
        assert agent._tts_enabled is False
        assert agent._utterance_queue is None

        payload_dict = {"speech": "降级模式", "emotion": "", "action": "", "metadata": {}}
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict))

        captured = await _wait_for_speech_event(bus)
        assert captured is not None, "tts_engine 缺失降级时仍应 emit 业务事件"
        assert captured.text == "降级模式"
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# TTS 启用：emit 与 TTS 队列复用同一 utterance_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamer_speech_and_tts_share_same_utterance_id():
    """TTS 启用时 STREAMER_SPEECH 与 TTS 入队使用同一 utterance_id。"""
    bus = EventBus()
    engine = _MockTTSEngine()
    agent = _build_streamer_agent_with_bus(
        event_bus=bus,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        captured: List[StreamerSpeechPayload] = []
        event = asyncio.Event()

        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            if isinstance(payload, StreamerSpeechPayload):
                captured.append(payload)
                event.set()

        bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

        payload_dict = {"speech": "复测一下 id", "emotion": "happy", "action": "", "metadata": {}}
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        await asyncio.wait_for(event.wait(), timeout=2.0)
        # 等 TTS 引擎取走
        for _ in range(50):
            if len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        assert len(captured) == 1
        speech_uid = captured[0].utterance_id
        assert speech_uid.startswith("utt_")
        assert captured[0].text == "复测一下 id"
        assert captured[0].emotion == "happy"

        # TTS 引擎收到的 utterance_id 与 STREAMER_SPEECH 一致
        assert len(engine.handle_speech_calls) == 1
        _, tts_uid = engine.handle_speech_calls[0]
        assert tts_uid == speech_uid
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# speech 空 / 仅空白：不 emit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_speech_does_not_emit_streamer_speech():
    """speech 空 / 仅空白 → 不 emit 业务事件（业务事实不存在）。"""
    bus = EventBus()
    agent = _build_streamer_agent_with_bus(
        event_bus=bus,
        tts_engine=None,
        speech_config=None,
    )
    await agent._on_start()
    try:
        for empty_speech in ["", "   ", "\n\t  "]:
            payload_dict = {"speech": empty_speech, "emotion": "", "action": "", "metadata": {}}
            agent._dispatch_speech_and_emotion(json.dumps(payload_dict))

        # 等 fire-and-forget 任务全部跑完
        await asyncio.sleep(0.05)

        # 不应有 STREAMER_SPEECH 事件（即使有 emotion 也不触发 streamer.speech，
        # streamer.speech 只表达 speech 业务事实）
        assert agent._utterance_seq == 0
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# context_service 写入历史
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamer_speech_writes_to_context_history():
    """speech 非空 + context_service 注入 → add_message 收到 ASSISTANT 消息。"""
    bus = EventBus()
    ctx = MagicMock()
    ctx.get_history = AsyncMock(return_value=[])
    ctx.add_message = AsyncMock(return_value=None)

    agent = _build_streamer_agent_with_bus(
        event_bus=bus,
        context_service=ctx,
        tts_engine=None,
        speech_config={"enabled": False},
    )
    await agent._on_start()
    try:
        payload_dict = {"speech": "写入历史", "emotion": "happy", "action": "", "metadata": {}}
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        # 等 fire-and-forget 历史写入任务跑完
        for _ in range(50):
            if ctx.add_message.await_count >= 1:
                break
            await asyncio.sleep(0.01)

        assert ctx.add_message.await_count == 1
        kwargs = ctx.add_message.await_args.kwargs
        assert kwargs["session_id"] == "live"
        assert kwargs["role"] == MessageRole.ASSISTANT
        assert kwargs["content"] == "写入历史"
        assert kwargs["emotion"] == "happy"
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_streamer_speech_skips_context_when_service_missing():
    """context_service=None → 跳过历史写入，不抛异常。"""
    bus = EventBus()
    # context_service=None 直接通过 _build_streamer_agent_with_bus 不传
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(success=False, content=""))
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    agent = StreamerAgent(
        config=_make_agent_config(),
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=None,  # 故意不注入
        event_bus=bus,
        tool_registry=None,
        speech_config={"enabled": False},
        tts_engine=None,
    )
    await agent._on_start()
    try:
        payload_dict = {"speech": "无 ctx 也行", "emotion": "", "action": "", "metadata": {}}
        # 不应抛异常
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))
        await asyncio.sleep(0.05)

        # 仍 emit 业务事件（与 context 缺失正交）
        received: List[StreamerSpeechPayload] = []
        event = asyncio.Event()

        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            if isinstance(payload, StreamerSpeechPayload):
                received.append(payload)
                event.set()

        bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)
        # 重新触发一次确保订阅能接到（前面那次可能已被 fire-and-forget 跑过）
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))
        await asyncio.wait_for(event.wait(), timeout=2.0)
        assert len(received) >= 1
    finally:
        await agent._on_stop()
