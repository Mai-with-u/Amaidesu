"""付费消息接线测试（B 线：SC / 礼物 / 上舰必进决策轮并带优先回应标注）。

覆盖需求：
- room.message.gift / super_chat / guard 三类事件 → StreamerAgent 订阅并进
  MessageBuffer，TimingGate 按类型判定 forced=true（类型驱动，非 importance）
- 决策轮以 forced=True 驱动 Planner（flush 路径透传 buffer.force）
- 普通 danmaku → forced=false，不误置强制标志
- Planner forced 分支 → 参考段含"优先回应"情境标注（钉住，防回归移除）
"""

from __future__ import annotations
import asyncio

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.time_utils import now_ms


def _build_agent(event_bus: Optional[EventBus] = None) -> StreamerAgent:
    """构造最小化 StreamerAgent：mock LLM / Prompt，真 TimingGate / MessageBuffer。"""
    llm = MagicMock()
    llm.call_tools = AsyncMock()
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "word_filter": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
        }
    )
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=event_bus,
    )


def _make_message(message_type: str, content: str = "") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,  # type: ignore[arg-type]
        user=RoomMessageUser(id="u1", name="测试观众"),
        content=content,
        timestamp_ms=now_ms(),
    )


# =============================================================================
# 付费事件 → 缓冲 + forced 判定
# =============================================================================


@pytest.mark.asyncio
async def test_paid_messages_buffered_with_forced_flag() -> None:
    """三类付费消息入缓冲且 TimingGate 判定 forced=true（类型驱动）。"""
    agent = _build_agent()
    for message_type in ("gift", "super_chat", "guard"):
        agent._buffer.drain()  # 清空上一类
        await agent.handle_message(_make_message(message_type, content="感谢主播"))
        assert agent._buffer.size == 1, message_type
        assert agent._buffer.force is True, message_type


@pytest.mark.asyncio
async def test_paid_events_subscribed_and_routed() -> None:
    """事件订阅接线：bus 上发三类付费事件 → 回调进缓冲（早退已去除）。"""
    bus = EventBus()
    try:
        agent = _build_agent(event_bus=bus)
        agent._subscribe_events()
        for message_type, event_name in (
            ("gift", CoreEvents.ROOM_MESSAGE_GIFT),
            ("super_chat", CoreEvents.ROOM_MESSAGE_SUPER_CHAT),
            ("guard", CoreEvents.ROOM_MESSAGE_GUARD),
        ):
            await bus.emit(
                event_name,
                _make_message(message_type, content="付费点名"),
                source="test",
            )
            await asyncio.sleep(0.05)
            assert agent._buffer.size == 1, message_type
            assert agent._buffer.force is True, message_type
            agent._buffer.drain()
    finally:
        await bus.cleanup()


# =============================================================================
# 决策轮：paid → forced=True 驱动 Planner；普通 danmaku → forced=False
# =============================================================================


@pytest.mark.asyncio
async def test_paid_message_flushes_decision_with_forced_true() -> None:
    """付费消息触发立即 flush，Planner 以 forced=True 进入决策轮。"""
    agent = _build_agent()
    plan_mock = AsyncMock(return_value=None)
    agent._rounds._planner.plan = plan_mock  # type: ignore[method-assign]

    await agent.handle_message(_make_message("super_chat", content="SC 点名"))
    await agent._maybe_flush()

    plan_mock.assert_awaited_once()
    assert plan_mock.await_args.kwargs.get("forced") is True


@pytest.mark.asyncio
async def test_normal_danmaku_not_forced_and_no_immediate_flush() -> None:
    """普通弹幕：不误置 forced，窗口未到期不触发决策轮。"""
    agent = _build_agent()
    plan_mock = AsyncMock(return_value=None)
    agent._rounds._planner.plan = plan_mock  # type: ignore[method-assign]

    await agent.handle_message(_make_message("danmaku", content="哈哈"))
    assert agent._buffer.force is False

    await agent._maybe_flush()
    plan_mock.assert_not_awaited()


# =============================================================================
# Planner forced 情境标注（钉住）
# =============================================================================


@pytest.mark.asyncio
async def test_planner_forced_context_contains_priority_annotation() -> None:
    """forced 批次 → 参考段含"优先回应"情境标注。"""
    planner = Planner(
        config={},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=MagicMock(),
        context_enabled=False,  # 裸路径：情境标注即整段参考文本
    )
    text = await planner._assemble_reference([], None, None, True, False, "")
    assert "优先回应" in text
    # 非 forced 批次不出现该标注
    plain = await planner._assemble_reference([], None, None, False, False, "")
    assert "优先回应" not in plain
