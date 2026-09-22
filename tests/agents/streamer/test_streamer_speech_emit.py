"""StreamerAgent 业务事件 ``streamer.speech`` 接线测试。

覆盖需求：
- speech 非空 + TTS 关闭：仍 emit ``streamer.speech``（业务事实与 TTS 启用正交）
- speech 非空 + TTS 启用：emit ``streamer.speech`` 且 utterance_id 与 TTS 队列复用同一 id
- speech 空 / 仅空白：不 emit（业务事实不存在）
- 发言落库（live_chat assistant 行）由 StorageLedger 订阅 streamer.speech 完成，
  不在本文件覆盖面内

Y 模型契约：``_dispatch_speech_and_emotion`` 入参 = ``reply_result.structured_content``（dict），
不是 JSON 字符串（reply_tool 已把 Replyer.generate 返回 dict 装入 structured_content）。

测试方法：用真 EventBus 订阅 ``streamer.speech``，在 ``_dispatch_speech_and_emotion``
后等待 fire-and-forget 任务完成（``asyncio.sleep``），验证收到的事件 payload。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.decision_executor import DecisionRoundExecutor
from src.agents.streamer.speech_dispatcher import SpeechDispatcher
from src.agents.streamer.stats import StreamerStats
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.llm.client import LLMResponse
from src.modules.llm.payload import Response


def _make_agent_config(**overrides: Any) -> StreamerConfig:
    """构造测试用 StreamerAgentConfig。"""
    defaults: Dict[str, Any] = {
        "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
        "proactive": {"enabled": False},
    }
    defaults.update(overrides)
    return StreamerConfig.from_dict(defaults)


def _build_streamer_agent_with_bus(
    *,
    event_bus: Optional[EventBus] = None,
    speech_config: Optional[Dict[str, Any]] = None,
    tts_engine: Optional[Any] = None,
) -> StreamerAgent:
    """构造最小化 StreamerAgent：注入真 EventBus + 可选 TTS。"""
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    llm.chat = AsyncMock()  # 兼容旧调用（不应被实际触发）
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = _make_agent_config()
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
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


def _subscribe_speech(event_bus: EventBus) -> tuple[asyncio.Event, List[StreamerSpeechPayload]]:
    """订阅 ``streamer.speech``，返回 (完成事件, 捕获列表)；dispatch 前调用。"""
    received: List[StreamerSpeechPayload] = []
    event = asyncio.Event()

    async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
        if isinstance(payload, StreamerSpeechPayload):
            received.append(payload)
            event.set()

    event_bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)
    return event, received


async def _wait_speech(
    event: asyncio.Event, received: List[StreamerSpeechPayload], timeout: float = 2.0
) -> Optional[StreamerSpeechPayload]:
    """等待已订阅的 speech 事件落地；超时返回 None。"""
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
        payload_dict = {
            "speech": "今天好冷",
            "emotion": "",
            "actions": [],
            "metadata": {},
        }
        speech_event, received = _subscribe_speech(bus)
        await agent._speech.dispatch(payload_dict)

        captured = await _wait_speech(speech_event, received)
        assert captured is not None, "TTS 关闭时仍应收到 streamer.speech"
        assert captured.text == "今天好冷"
        # emotion 必有值契约：旧空串输入按生产者同款语义规范化为 neutral
        assert captured.emotion == "neutral"
        assert captured.emotion_intensity == 0.5
        assert captured.utterance_id.startswith("utt_")
        # TTS 关闭时 _utterance_queue 仍为 None
        assert agent._speech.utterance_queue is None
        # seq 已自增
        assert agent._speech.utterance_seq == 1
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
        assert agent._speech.tts_enabled is False
        assert agent._speech.utterance_queue is None

        payload_dict = {
            "speech": "降级模式",
            "emotion": "",
            "actions": [],
            "metadata": {},
        }
        speech_event, received = _subscribe_speech(bus)
        await agent._speech.dispatch(payload_dict)

        captured = await _wait_speech(speech_event, received)
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

        payload_dict = {
            "speech": "复测一下 id",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        await agent._speech.dispatch(payload_dict)

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
            payload_dict = {
                "speech": empty_speech,
                "emotion": "",
                "actions": [],
                "metadata": {},
            }
            await agent._speech.dispatch(payload_dict)

        # 等 fire-and-forget 任务全部跑完
        await asyncio.sleep(0.05)

        # 不应有 STREAMER_SPEECH 事件（即使有 emotion 也不触发 streamer.speech，
        # streamer.speech 只表达 speech 业务事实）
        assert agent._speech.utterance_seq == 0
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# 顺序契约：speech 先于轮末 decision 与 idle 状态发出
# ---------------------------------------------------------------------------

_REPLY_OUTCOME = {
    "replied": True,
    "target": "m1",
    "reply_to": "m1",
    "topic_summary": "闲聊",
    "reply_guidance": "热情一点",
    "confidence": 0.9,
    "silent_reason": None,
    "reply_duration_ms": 12,
    "reply_payload": {"speech": "你好呀", "emotion": {"name": "happy", "intensity": 0.5}, "actions": []},
}


@pytest.mark.asyncio
async def test_speech_event_emitted_before_round_end_events():
    """``streamer.speech`` 同步发出：先于本轮 ``planner.decision`` 与 idle 状态。

    控制台时间线按实际发生顺序渲染依赖该先后契约。用真 EventBus + 真
    SpeechDispatcher 跑一轮决策，按事件到达序断言（emit 后台 handler
    在首个 await 前先入队，FIFO 顺序即发布顺序）。
    """
    bus = EventBus()
    dispatcher = SpeechDispatcher(event_bus=bus, subtitle_service=None, tts_engine=None, speech_config=None)
    planner = MagicMock()
    planner.plan = AsyncMock(return_value=dict(_REPLY_OUTCOME))
    planner.last_raw_content = "RAW"
    planner.last_request_id = "req_1"
    executor = DecisionRoundExecutor(
        planner=planner,
        speech=dispatcher,
        event_bus=bus,
        room_state=MagicMock(),
        proactive_trigger=MagicMock(),
        stats=StreamerStats(),
        thinking_sink=None,
        thinking_enabled=False,
        history_provider=AsyncMock(return_value=[]),
        rundown_text_provider=MagicMock(return_value=None),
        game_narrative_provider=MagicMock(return_value=""),
    )

    order: List[str] = []

    def _make_capture(label: str):
        async def _capture(event_name: str, payload: Any, source: Optional[str] = None) -> None:
            order.append(label)

        return _capture

    bus.on(CoreEvents.STREAMER_STAGE, _make_capture("stage"), model_class=StreamerStagePayload)
    bus.on(CoreEvents.STREAMER_SPEECH, _make_capture("speech"), model_class=StreamerSpeechPayload)
    bus.on(CoreEvents.PLANNER_DECISION, _make_capture("decision"), model_class=PlannerDecisionPayload)

    batch = [MagicMock(message_id="m1", content="主播好", user=MagicMock(id="u1", name="观众"))]
    result = await executor.execute(batch, forced=False, trigger_reason="batch:flush")
    assert result["speech"] == "你好呀"

    # 事件 handler 是 emit 派发的后台任务，让出一拍等它们全部落地
    for _ in range(50):
        if len(order) >= 4:
            break
        await asyncio.sleep(0.01)

    # planning 状态 → 发言 → 轮末决策记录 → idle 状态
    assert order == ["stage", "speech", "decision", "stage"]
