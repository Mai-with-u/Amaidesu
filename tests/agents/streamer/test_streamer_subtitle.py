"""StreamerAgent 字幕基础设施接线测试。

覆盖需求：

- speech 非空 + 字幕服务注入 → ``subtitle_service.show`` 被以
  ``(text, utterance_id)`` 调用一次，且 ``utterance_id`` 与
  ``streamer.speech`` 业务事件 / TTS 队列一致
- ``subtitle_service=None`` → 跳过字幕调用，不抛异常
- speech 为空 → 不触发字幕调用（业务事实不存在）
- 字幕服务抛异常 → 决策循环不受影响（WARN 日志兜底）

测试方法：构造最小化 StreamerAgent（注入 mock 字幕服务 + 可选
TTS / EventBus），直接调用 ``_dispatch_speech_and_emotion`` 验证
字幕分发逻辑。AsyncMock 录制 ``show`` 调用，模拟字幕服务返回。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
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


def _build_streamer_agent(
    *,
    event_bus: Optional[EventBus] = None,
    speech_config: Optional[Dict[str, Any]] = None,
    tts_engine: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
) -> StreamerAgent:
    """构造最小化 StreamerAgent：mock LLM/Prompt/Context/字幕。"""
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(success=False, content=""))
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    ctx = MagicMock()
    ctx.get_history = AsyncMock(return_value=[])
    ctx.add_message = AsyncMock(return_value=None)

    return StreamerAgent(
        config=_make_agent_config(),
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=ctx,
        event_bus=event_bus,
        tool_registry=None,
        speech_config=speech_config,
        tts_engine=tts_engine,
        subtitle_service=subtitle_service,
    )


class _MockTTSEngine:
    """TTS 引擎 mock：录制所有 handle_speech 调用。"""

    def __init__(self) -> None:
        self.handle_speech_calls: List[tuple[str, Optional[str]]] = []

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.handle_speech_calls.append((text, utterance_id))


# ---------------------------------------------------------------------------
# subtitle_service 注入 + 正确参数调用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subtitle_service_show_called_with_speech_and_utterance_id():
    """speech 非空 + 字幕服务注入 → ``subtitle_service.show(text, utterance_id)`` 被调一次。"""
    subtitle_service = MagicMock()
    subtitle_service.show = AsyncMock(return_value=None)

    bus = EventBus()
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        event_bus=bus,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
        subtitle_service=subtitle_service,
    )
    await agent._on_start()
    try:
        payload_dict = {
            "speech": "字幕测试文本",
            "emotion": "happy",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        # 等 fire-and-forget 字幕任务跑完
        for _ in range(50):
            if subtitle_service.show.await_count >= 1:
                break
            await asyncio.sleep(0.01)

        assert subtitle_service.show.await_count == 1
        call_args = subtitle_service.show.await_args
        # 调用参数：(text, utterance_id)
        assert call_args.args[0] == "字幕测试文本"
        uid = call_args.args[1]
        assert isinstance(uid, str) and uid.startswith("utt_"), (
            f"utterance_id 应为 utt_ 前缀字符串，实际 {uid!r}"
        )
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_subtitle_service_shares_utterance_id_with_streamer_speech_and_tts():
    """字幕服务 ``show`` 收到的 ``utterance_id`` 与 STREAMER_SPEECH / TTS 队列一致。"""
    subtitle_service = MagicMock()
    subtitle_service.show = AsyncMock(return_value=None)

    bus = EventBus()
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        event_bus=bus,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
        subtitle_service=subtitle_service,
    )
    await agent._on_start()
    try:
        captured: List[StreamerSpeechPayload] = []
        speech_event = asyncio.Event()

        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            if isinstance(payload, StreamerSpeechPayload):
                captured.append(payload)
                speech_event.set()

        bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

        payload_dict = {
            "speech": "id 关联校验",
            "emotion": "neutral",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        # 等三路全部落地（事件 / TTS / 字幕）
        await asyncio.wait_for(speech_event.wait(), timeout=2.0)
        for _ in range(50):
            if subtitle_service.show.await_count >= 1 and len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        assert len(captured) == 1
        speech_uid = captured[0].utterance_id

        _, tts_uid = engine.handle_speech_calls[0]
        subtitle_uid = subtitle_service.show.await_args.args[1]

        assert tts_uid == speech_uid
        assert subtitle_uid == speech_uid
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# subtitle_service=None / 缺注入：跳过 + 不抛
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subtitle_service_none_skips_show_without_error():
    """``subtitle_service=None`` → 跳过字幕调用，不抛异常。"""
    bus = EventBus()
    agent = _build_streamer_agent(
        event_bus=bus,
        tts_engine=None,
        speech_config={"enabled": False},
        subtitle_service=None,
    )
    await agent._on_start()
    try:
        payload_dict = {"speech": "无字幕也行", "emotion": "", "action": "", "metadata": {}}
        # 不应抛异常
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))
        await asyncio.sleep(0.05)
        # 字段存储为 None
        assert agent._subtitle_service is None
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# speech 空 / 仅空白：不触发字幕
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_speech_does_not_trigger_subtitle():
    """speech 空 / 仅空白 → 不触发字幕调用（业务事实不存在）。"""
    subtitle_service = MagicMock()
    subtitle_service.show = AsyncMock(return_value=None)

    bus = EventBus()
    agent = _build_streamer_agent(
        event_bus=bus,
        tts_engine=None,
        speech_config={"enabled": False},
        subtitle_service=subtitle_service,
    )
    await agent._on_start()
    try:
        for empty_speech in ["", "   ", "\n\t  "]:
            payload_dict = {"speech": empty_speech, "emotion": "", "action": "", "metadata": {}}
            agent._dispatch_speech_and_emotion(json.dumps(payload_dict))

        # 等可能的 fire-and-forget 任务全部跑完
        await asyncio.sleep(0.05)

        assert subtitle_service.show.await_count == 0, (
            "空 speech 不应触发 subtitle_service.show"
        )
        # seq 不递增
        assert agent._utterance_seq == 0
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# 字幕服务抛异常：决策循环不受影响
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subtitle_service_exception_does_not_break_decision_loop(loguru_capture):
    """字幕服务 ``show`` 抛异常 → _dispatch 兜底，决策循环不受影响。"""
    subtitle_service = MagicMock()
    subtitle_service.show = AsyncMock(side_effect=RuntimeError("simulated subtitle failure"))

    bus = EventBus()
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        event_bus=bus,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
        subtitle_service=subtitle_service,
    )
    await agent._on_start()
    try:
        payload_dict = {"speech": "字幕坏了", "emotion": "", "action": "", "metadata": {}}
        # 不应抛异常
        agent._dispatch_speech_and_emotion(json.dumps(payload_dict, ensure_ascii=False))

        # 等字幕任务失败 + TTS 入队执行
        for _ in range(50):
            if subtitle_service.show.await_count >= 1 and len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        assert subtitle_service.show.await_count == 1
        # TTS 仍正常入队（字幕失败不阻断语音）
        assert len(engine.handle_speech_calls) == 1
        # 应记 WARN 日志
        warn_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "WARNING" and "字幕 show 异常" in r["message"]
        ]
        assert warn_records, (
            "字幕服务抛异常应记录 WARN 日志，实际: "
            f"{[r['message'] for r in loguru_capture.records]}"
        )
    finally:
        await agent._on_stop()
