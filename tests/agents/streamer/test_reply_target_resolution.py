"""StreamerAgent 从 batch 反查"本次回复观众 user_id"链路测试。

覆盖需求：
- ``_resolve_reply_target_user`` 五种匹配/兜底分支
- ``_emit_streamer_speech`` 接受 ``target_user_id`` 参数并透传到
  ``StreamerSpeechPayload``（业务事件 ``streamer.speech``）

测试方法：构造最小可用 ``StreamerAgent`` 实例（不调 ``_on_start``），
对反查方法直接同步调用；对 emit 走真 ``EventBus`` + 等待 fire-and-forget
任务完成（沿用 ``test_streamer_speech_emit.py`` 的等待模式）。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.types.base.normalized_message import NormalizedMessage


def _make_minimal_agent(*, event_bus: Optional[EventBus] = None) -> StreamerAgent:
    """构造测试用最小 StreamerAgent（不调 _on_start）。"""
    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        agenda_enabled=False,
        profanity_enabled=False,
        batch_window_ms=100,
        tick_interval_ms=50,
    )
    return StreamerAgent(
        config=config,
        llm_manager=MagicMock(),
        prompt_manager=MagicMock(),
        context_service=None,
        event_bus=event_bus,
        tool_registry=None,
        speech_config=None,
        tts_engine=None,
    )


def _make_msg(
    *,
    text: str,
    user_id: str,
    message_id: Optional[str] = None,
) -> NormalizedMessage:
    """构造测试用 NormalizedMessage。"""
    return NormalizedMessage(
        text=text,
        source="test",
        data_type="text",
        importance=0.5,
        user_id=user_id,
        user_nickname=f"user_{user_id}",
        message_id=message_id or f"auto_{text}_{user_id}",
    )


# ---------------------------------------------------------------------------
# _resolve_reply_target_user：5 个匹配/兜底分支
# ---------------------------------------------------------------------------


def test_resolve_target_by_message_id():
    """plan.target 命中 batch 中某条消息的 message_id → 返回该消息的 user_id。"""
    agent = _make_minimal_agent()
    msg = _make_msg(text="主播好", user_id="alice", message_id="msg_123")
    plan = DecisionPlan(target="msg_123")

    assert agent._resolve_reply_target_user(plan, [msg]) == "alice"


def test_resolve_target_by_text_fallback():
    """plan.target 未命中 message_id，但命中 text 子串 → 返回 user_id。"""
    agent = _make_minimal_agent()
    msg = _make_msg(text="你好呀主播", user_id="bob")
    plan = DecisionPlan(target="你好呀")

    assert agent._resolve_reply_target_user(plan, [msg]) == "bob"


def test_resolve_target_none_when_no_target():
    """plan.target=None → 返回 None（主动发言/无特定目标）。"""
    agent = _make_minimal_agent()
    msg = _make_msg(text="hi", user_id="alice")
    plan = DecisionPlan(target=None)

    assert agent._resolve_reply_target_user(plan, [msg]) is None


def test_resolve_target_fallback_last_message():
    """plan.target 全未命中 → 返回 batch 最后一条的 user_id（保守兜底）。"""
    agent = _make_minimal_agent()
    m1 = _make_msg(text="aaa", user_id="alice")
    m2 = _make_msg(text="bbb", user_id="bob")
    m3 = _make_msg(text="ccc", user_id="carol")
    plan = DecisionPlan(target="zzz_unmatched")

    assert agent._resolve_reply_target_user(plan, [m1, m2, m3]) == "carol"


def test_resolve_target_empty_batch():
    """batch=[] → 返回 None（无消息可兜底）。"""
    agent = _make_minimal_agent()
    plan = DecisionPlan(target="msg_123")

    assert agent._resolve_reply_target_user(plan, []) is None


# ---------------------------------------------------------------------------
# _emit_streamer_speech：透传 target_user_id 到 StreamerSpeechPayload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_streamer_speech_passes_target():
    """_emit_streamer_speech 接受 target_user_id 并透传到 StreamerSpeechPayload。"""
    bus = EventBus()
    captured: List[StreamerSpeechPayload] = []
    captured_event = asyncio.Event()

    async def _capture(event_name, payload, source=None):
        if isinstance(payload, StreamerSpeechPayload):
            captured.append(payload)
            captured_event.set()

    bus.on(CoreEvents.STREAMER_SPEECH, _capture, model_class=StreamerSpeechPayload)

    agent = _make_minimal_agent(event_bus=bus)
    agent._emit_streamer_speech("utt_1", "你好", "neutral", "viewer123")

    await asyncio.wait_for(captured_event.wait(), timeout=2.0)
    assert len(captured) == 1
    assert captured[0].utterance_id == "utt_1"
    assert captured[0].text == "你好"
    assert captured[0].emotion == "neutral"
    assert captured[0].target_user_id == "viewer123"
