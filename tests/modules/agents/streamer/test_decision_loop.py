"""StreamerAgent 决策循环集成测试（Planner ReAct 架构）。

Planner 以 ReAct 循环运行（``llm.chat_messages`` + 全局工具面 + reply 局部工具）；
Replyer 仍是 ``llm.call_tools(tools=[reply])``。测试 mock 同步对齐：
- Planner LLM 响应 = ``chat_messages`` 返回完整 OpenAI 形态 tool_calls
  （``{id, type, function: {name, arguments}}``）
- Replyer LLM 响应 = ``call_tools`` 返回 reply tool_call

决策流：Planner 循环内调 reply（经 _reply_provider.invoke → Replyer.generate）；
自然终止（无 tool_calls）= 静默。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.llm.manager import LLMResponse
from src.modules.tools import ToolRegistry, ToolInvocation
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload


def _make_payload(text: str = "主播好可爱") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众A"),
        content=text,
    )


def _make_normalized(text: str = "主播好可爱") -> NormalizedMessage:
    return NormalizedMessage(
        text=text,
        source="bilibili",
        data_type="text",
        importance=0.5,
        user_id="u1",
        user_nickname="观众A",
    )


# ---------------------------------------------------------------------------
# LLMResponse 工厂（Planner ReAct：chat_messages 完整形态 / Replyer：call_tools）
# ---------------------------------------------------------------------------


def _planner_tool_call(name: str, args: dict, call_id: str = "call_p1") -> dict:
    """构造 Planner 的完整 OpenAI 形态 tool_call。"""
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _planner_react_response(tool_calls: list) -> LLMResponse:
    """构造 Planner 的 chat_messages 响应（完整 tool_calls；空列表 = 自然终止）。"""
    return LLMResponse(success=True, content="", tool_calls=tool_calls)


def _replyer_response(speech: str, emotion: str = "happy", actions: list | None = None) -> LLMResponse:
    """构造 Replyer 的 call_tools 响应（tool_calls[0] = reply）。"""
    tool_calls = [
        {
            "name": "reply",
            "arguments": json.dumps({"speech": speech, "emotion": emotion}, ensure_ascii=False),
        }
    ]
    if actions:
        for action in actions:
            tool_calls.append(
                {
                    "name": action["name"],
                    "arguments": json.dumps(action.get("parameters", {}), ensure_ascii=False),
                }
            )
    return LLMResponse(success=True, content="", tool_calls=tool_calls)


def _replyer_failure(reason: str = "mock failure") -> LLMResponse:
    """构造 Replyer LLM 失败响应（success=False）。"""
    return LLMResponse(success=False, content=None, error=reason)


# ---------------------------------------------------------------------------
# Agent 装配（Planner ReAct + Replyer call_tools）
# ---------------------------------------------------------------------------


def _setup_agent(
    chat_responses: list | None = None,
    config_overrides: dict | None = None,
) -> tuple[StreamerAgent, EventBus, ToolRegistry, MagicMock, MagicMock]:
    """构造完整测试 Agent：mock LLM（chat_messages=Planner / call_tools=Replyer）。

    默认：Planner 首步直接调 reply；Replyer 产出 "谢谢支持！" + happy。
    """
    if chat_responses is None:
        chat_responses = [
            _planner_react_response(
                [
                    _planner_tool_call(
                        "streamer_reply",
                        {
                            "topic_summary": "主播好可爱",
                            "reply_guidance": "回应夸奖",
                            "target": "u1",
                            "confidence": 0.9,
                        },
                    )
                ]
            )
        ]
    replyer_resp = _replyer_response("谢谢支持！", emotion="happy")

    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=list(chat_responses))
    llm.call_tools = AsyncMock(return_value=replyer_resp)

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        **{
            "planner_llm": "llm_fast",
            "replyer_llm": "llm",
            "proactive_enabled": False,
            "profanity_enabled": False,
            "batch_window_ms": 100,
            "tick_interval_ms": 50,
            **(config_overrides or {}),
        }
    )

    bus = EventBus()
    registry = ToolRegistry()
    context = MagicMock()
    context.get_history = AsyncMock(return_value=[])

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context,
        event_bus=bus,
        tool_registry=registry,
    )

    return agent, bus, registry, llm, prompt


@pytest.mark.asyncio
async def test_decision_loop_danmaku_to_reply_provider():
    """决策循环端到端：弹幕事件 → Planner chat_messages（ReAct 调 reply）→ Replyer.call_tools → 发言管线。

    reply 是真工具：注册进 ToolRegistry（名单 [streamer]）；Planner 循环内暂仍经
    _reply_provider.invoke 直连（调用统一在后续任务收口）。proactive/command 是
    代码直连的内部件，不注册（§5 判据）。
    """
    agent, bus, registry, llm, prompt = _setup_agent()

    await agent.start()
    try:
        # 工具声明（Agent 面向审计的口径）
        spec_names = {spec.name for spec in agent.list_tools()}
        assert spec_names == {"reply"}  # 只有真工具进声明（proactive/command 是内部件）

        registered = {spec.full_name for spec in registry.list_tools()}
        assert "streamer_reply" in registered  # reply 已注册（名单 [streamer]）
        assert "should_speak_proactively" not in registered
        assert "parse_command" not in registered

        # 1. 投放一条弹幕事件
        payload = _make_payload("主播好可爱！")
        await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="bilibili")

        # 给 Agent 一些时间处理事件 + flush 循环
        await asyncio.sleep(0.2)

        # 2. Planner ReAct 至少一轮 chat_messages
        assert llm.chat_messages.await_count >= 1, "Planner 应至少调一次 chat_messages"

        # 3. 循环内调 reply → Replyer 生成（call_tools）
        assert llm.call_tools.await_count >= 1, "Planner 调 reply 后 Replyer 应被触发"

        # 4. reply_provider 已构造（循环内直连 invoke）
        assert agent._reply_provider is not None

        # 5. 验证统计计数
        stats = agent.get_statistics()
        assert stats["total_messages"] >= 1
        assert stats["total_replies"] >= 1
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_planner_no_reply_path():
    """Planner 自然终止（无 tool_calls）→ 不触发 Replyer.call_tools，静默收场。"""
    chat_responses = [_planner_react_response([])]

    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=list(chat_responses))
    llm.call_tools = AsyncMock()

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        profanity_enabled=False,
        batch_window_ms=100,
        tick_interval_ms=50,
    )

    bus = EventBus()
    registry = ToolRegistry()
    context = MagicMock()
    context.get_history = AsyncMock(return_value=[])

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context,
        event_bus=bus,
        tool_registry=registry,
    )

    await agent.start()
    try:
        await bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            _make_payload("test"),
            source="bilibili",
        )

        await asyncio.sleep(0.2)

        # Planner 恰好 1 轮 chat_messages（自然终止），Replyer 不调
        assert llm.chat_messages.await_count == 1, "自然终止应恰好 1 轮"
        assert llm.call_tools.await_count == 0, "自然终止时 Replyer 不应被触发"

        stats = agent.get_statistics()
        assert stats["total_no_action"] >= 1
    finally:
        await agent.cleanup()


@pytest.mark.asyncio

@pytest.mark.asyncio
async def test_decision_loop_proactive_gated_until_live_started():
    """主动发言场次闸：开播前静默且 pending 信号保留，开播后首个 tick 触发。"""
    agent, bus, registry, llm, prompt = _setup_agent(
        chat_responses=[_planner_react_response([])],
        config_overrides={"proactive_enabled": True},
    )

    await agent.start()
    try:
        agent._rundown_proactive_pending = True

        await asyncio.sleep(0.2)
        assert agent._total_proactive == 0, "未开播时主动发言应静默"
        assert agent._rundown_proactive_pending is True, "pending 信号不应被消费"

        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=1, source="manual", title="测试场"),
            source="test",
        )
        await asyncio.sleep(0.2)

        assert agent._live_active is True
        assert agent._total_proactive >= 1, "开播后首个 tick 应消费保留的 pending 触发主动发言"
        assert agent._rundown_proactive_pending is False
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_proactive_gated_after_live_ended():
    """下播收闸：live.ended 后主动发言静默，弹幕回复不受影响。"""
    agent, bus, registry, llm, prompt = _setup_agent(
        chat_responses=[_planner_react_response([])],
        config_overrides={"proactive_enabled": True},
    )

    await agent.start()
    try:
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=1),
            source="test",
        )
        await asyncio.sleep(0.1)
        assert agent._live_active is True

        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=1, reason="手动结束"),
            source="test",
        )
        await asyncio.sleep(0.1)
        assert agent._live_active is False

        agent._rundown_proactive_pending = True
        baseline = agent._total_proactive
        await asyncio.sleep(0.3)
        assert agent._total_proactive == baseline, "下播后主动发言应静默（无新增触发）"
        assert agent._rundown_proactive_pending is True, "下播后的 pending 信号保留待下场"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_danmaku_reply_not_gated_by_live_session():
    """场次闸只挡主动发言分支：弹幕回复路径不受开播状态影响。"""
    agent, bus, registry, llm, prompt = _setup_agent()

    await agent.start()
    try:
        await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("主播好可爱！"), source="bilibili")
        await asyncio.sleep(0.2)
        assert llm.chat_messages.await_count >= 1, "未开播时弹幕回复不应被门控"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio

@pytest.mark.asyncio
async def test_decision_loop_handle_message_direct():
    """handle_message 直接入口（测试用）：跳过 EventBus，直接调 Agent。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(return_value=_planner_react_response([]))
    llm.call_tools = AsyncMock(return_value=_replyer_response("OK", emotion="happy"))
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        profanity_enabled=False,
        batch_window_ms=100,
        tick_interval_ms=50,
    )

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=None,
        event_bus=None,
        tool_registry=ToolRegistry(),
    )

    # 不启动（不订阅事件），直接 handle_message
    await agent.handle_message(_make_normalized("直接调用"))

    # 统计：消息已入缓冲
    assert agent.get_statistics()["total_messages"] == 1


class TestDecisionObservability:
    """决策可观测收口：每轮决策恰好一条 planner.decision + 阶段事件成对。"""

    @pytest.mark.asyncio
    async def test_decision_round_emits_decision_and_stage_events(self):
        agent, bus, registry, llm, prompt = _setup_agent()

        decisions: list = []
        stages: list = []

        async def _on_decision(name, payload, source):
            decisions.append(payload)

        async def _on_stage(name, payload, source):
            stages.append(payload)

        bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)
        bus.on(CoreEvents.STREAMER_STAGE, _on_stage, model_class=StreamerStagePayload)

        await agent.start()
        try:
            payload = _make_payload("主播好可爱！")
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="bilibili", wait=True)
            await asyncio.sleep(0.3)

            assert len(decisions) == 1, f"每轮决策应恰好一条 planner.decision，实际 {len(decisions)}"
            d = decisions[0]
            assert d.round_id.startswith("rnd_")
            assert d.should_reply is True
            assert d.utterance_id and d.utterance_id.startswith("utt_")
            assert d.error is None
            assert d.silent_reason is None
            assert d.total_duration_ms >= 0
            assert isinstance(d.batch, list) and len(d.batch) == 1
            assert d.batch[0].user_name == "观众A"

            assert [s.stage for s in stages] == ["planning", "idle"], "阶段事件应成对（planning → idle）"
            assert stages[0].agent_state == "running"
            assert stages[1].agent_state == "wait"
            assert stages[0].round_id == d.round_id
        finally:
            await agent.stop()
            await bus.cleanup()

    @pytest.mark.asyncio
    async def test_planner_failure_still_emits_decision_event(self):
        """Planner LLM 失败也必须发决策事件——失败可见性是核心价值。"""
        agent, bus, registry, llm, prompt = _setup_agent()
        llm.chat_messages = AsyncMock(side_effect=RuntimeError("boom"))

        decisions: list = []

        async def _on_decision(name, payload, source):
            decisions.append(payload)

        bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)

        await agent.start()
        try:
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("hi"), source="t", wait=True)
            await asyncio.sleep(0.3)

            assert len(decisions) == 1
            d = decisions[0]
            assert d.should_reply is False
            assert d.error is not None
            assert "llm_error" in d.error
        finally:
            await agent.stop()
            await bus.cleanup()

    @pytest.mark.asyncio
    async def test_reply_to_flows_into_decision_event(self):
        """reply 意图的 target → 决策事件携带 reply_to_message_id（互动分析关联键）。"""
        agent, bus, registry, llm, prompt = _setup_agent(
            chat_responses=[
                _planner_react_response(
                    [
                        _planner_tool_call(
                            "streamer_reply",
                            {
                                "topic_summary": "回应夸奖",
                                "reply_guidance": "回应夸奖",
                                "target": "msg_abc",
                                "confidence": 0.9,
                            },
                            call_id="call_r2",
                        )
                    ]
                )
            ]
        )

        decisions: list = []

        async def _on_decision(name, payload, source):
            decisions.append(payload)

        bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)

        await agent.start()
        try:
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("主播好可爱"), source="t", wait=True)
            await asyncio.sleep(0.3)

            assert len(decisions) == 1
            assert decisions[0].reply_to_message_id == "msg_abc"
        finally:
            await agent.stop()
            await bus.cleanup()
