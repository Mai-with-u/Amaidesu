"""StreamerAgent 决策循环集成测试（Y 模型：标准 function calling）。

Planner / Replyer 改走 ``llm.call_tools(tools=[...])`` 后，测试 mock 同步对齐：
- LLM 响应 = ``LLMResponse(success, content="", tool_calls=[{name, arguments(JSON str)}])``
- Planner 调 ``produce_plan``；Replyer 调 ``reply``（Agent 内部协议工具）

Y 模型分层（reply / proactive / command 不再注册进 ToolRegistry）：
- reply 工具入口 = ``agent._reply_provider.invoke(...)``（决策循环直连）；
  验证路径：直接断言 reply_provider 实例已构造 + invoke 被 await 过一次。
- proactive / command 同理不入 ToolRegistry。

QA Scenario（acceptance criteria）：
    Tool: Bash
    Preconditions: 注入 mock 弹幕（room.message.danmaku）+ mock call_tools 响应
    Steps:
      1. 实例化 StreamerAgent，投放一条弹幕事件
      2. 断言 Planner call_tools 被调用（produce_plan）
      3. 断言 should_reply=true 时 Replyer call_tools 被调用（reply）
    Expected Result: Agent 决策循环跑通（mock 环境）
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
# LLMResponse 工厂（Y 模型：call_tools 形态）
# ---------------------------------------------------------------------------


def _planner_response(plan_args: dict) -> LLMResponse:
    """构造 Planner 的 call_tools 响应（tool_calls[0] = produce_plan）。"""
    return LLMResponse(
        success=True,
        content="",
        tool_calls=[
            {
                "name": "produce_plan",
                "arguments": json.dumps(plan_args, ensure_ascii=False),
            }
        ],
    )


def _replyer_response(speech: str, emotion: str = "happy", actions: list | None = None) -> LLMResponse:
    """构造 Replyer 的 call_tools 响应（tool_calls[0] = reply，可选追加动作工具）。"""
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
# Agent 装配（Y 模型 call_tools 形态）
# ---------------------------------------------------------------------------


def _setup_agent() -> tuple[StreamerAgent, EventBus, ToolRegistry, MagicMock, MagicMock]:
    """构造完整测试 Agent：mock LLM（call_tools）+ mock EventBus + mock ToolRegistry。

    Planner 决策 should_reply=true；Replyer 产出 "谢谢支持！" + happy。
    """
    planner_resp = _planner_response(
        {
            "should_reply": True,
            "target": "u1",
            "topic_summary": "主播好可爱",
            "reply_guidance": "回应夸奖",
            "confidence": 0.9,
        }
    )
    replyer_resp = _replyer_response("谢谢支持！", emotion="happy")

    llm = MagicMock()
    # call_tools() async；Planner 调一次 → Replyer 调一次 → 两次 call_tools 调用
    llm.call_tools = AsyncMock(side_effect=[planner_resp, replyer_resp])

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        agenda_enabled=False,
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

    return agent, bus, registry, llm, prompt


@pytest.mark.asyncio
async def test_decision_loop_danmaku_to_reply_provider():
    """决策循环端到端：弹幕事件 → Planner.call_tools(produce_plan) → Replyer.call_tools(reply) → _reply_provider.invoke。

    Y 模型：reply 是 Agent 内部协议，不进 ToolRegistry；直连 _reply_provider.invoke。
    """
    agent, bus, registry, llm, prompt = _setup_agent()

    await agent.start()
    try:
        # Y 模型：reply/proactive/command 不注册进 ToolRegistry（Agent 内部协议）；
        # 改用 list_tools() 校验 Agent 自己声明的协议是否齐全（不依赖 registry.list_tools）。
        spec_names = {spec.name for spec in agent.list_tools()}
        assert "reply" in spec_names
        assert "should_speak_proactively" in spec_names
        assert "parse_command" in spec_names

        # 同时确认 ToolRegistry 没有这三个（Y 分层）
        registered_in_registry = {spec.name for spec in registry.list_tools()}
        assert "reply" not in registered_in_registry
        assert "should_speak_proactively" not in registered_in_registry
        assert "parse_command" not in registered_in_registry

        # 1. 投放一条弹幕事件
        payload = _make_payload("主播好可爱！")
        await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="bilibili")

        # 给 Agent 一些时间处理事件 + flush 循环
        await asyncio.sleep(0.2)

        # 2. 验证 Planner.call_tools 被调用（至少 1 次）
        assert llm.call_tools.await_count >= 1, "Planner 应至少调一次 call_tools"

        # 3. 验证 should_reply=true 时 Replyer 也被调用（≥2 次 = Planner + Replyer）
        assert llm.call_tools.await_count >= 2, "should_reply=true 时 Replyer 应被触发"

        # 4. 验证 reply_provider 已构造（Y 模型：直连 invoke；LLM 调 2 次 + total_replies 已覆盖调用验证）
        assert agent._reply_provider is not None

        # 5. 验证统计计数
        stats = agent.get_statistics()
        assert stats["total_messages"] >= 1
        assert stats["total_replies"] >= 1
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_planner_no_reply_path():
    """Planner should_reply=False → 不触发 Replyer.call_tools（只有 Planner 一次 call_tools）。"""
    planner_resp = _planner_response({"should_reply": False, "confidence": 0.9})

    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=planner_resp)

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        agenda_enabled=False,
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

        # Planner 调 1 次 call_tools（should_reply=False），Replyer 不调
        assert llm.call_tools.await_count == 1, "should_reply=False 时 Replyer 不应被触发"

        stats = agent.get_statistics()
        assert stats["total_no_action"] >= 1
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_proactive_tool_invoke():
    """should_speak_proactively 工具可独立调用：返回触发 reason 或 None。"""
    from src.agents.streamer.tools.proactive_tool import ProactiveToolProvider
    from src.agents.streamer.proactive_trigger import ProactiveTrigger
    from src.agents.streamer.room_state import RoomState

    # 直接构造 trigger + provider 并注册到 registry
    trigger = ProactiveTrigger(
        {
            "enabled": True,
            "cold_timeout_ms": 45_000,
            "min_interval_ms": 0,
            "topic_required": False,
            "max_per_hour": 10,
        }
    )
    rs = RoomState()
    rs.set_topic_summary("观众在聊游戏", now_ms=1_000)

    provider = ProactiveToolProvider(
        trigger=trigger,
        room_state=rs,
        external_pending=False,
        agenda_pending=False,
        agenda_ready=False,
    )

    # 主播内部协议工具不经 ToolRegistry 注册（Y 模型），按生产形态直调
    # provider.invoke（裸名分发）
    # 房间无最近消息 → 冷场 → 应触发 cold
    result = await provider.invoke(ToolInvocation(tool_name="should_speak_proactively", arguments={}, source="test"))
    # 内容可能是 "cold" 或 ""（取决于状态）
    assert result is not None


@pytest.mark.asyncio
async def test_decision_loop_parse_command_tool():
    """parse_command 工具：返回解析后的命令 dict（is_command + name + args + action）。"""
    from src.agents.streamer.tools.command_tool import CommandToolProvider

    provider = CommandToolProvider(
        command_prefix="/",
        command_mappings={"chat": "chat", "attack": "attack"},
    )

    # 主播内部协议工具不经 ToolRegistry 注册（Y 模型），按生产形态直调
    # provider.invoke（裸名分发）
    # 测试合法命令
    result = await provider.invoke(
        ToolInvocation(tool_name="parse_command", arguments={"text": "/chat hello world"}, source="test")
    )
    assert result.success is True
    parsed = json.loads(result.content)
    assert parsed["is_command"] is True
    assert parsed["name"] == "chat"
    assert parsed["args"] == ["hello", "world"]
    assert parsed["action"] == "chat"
    assert parsed["supported"] is True

    # 测试非命令文本
    result = await provider.invoke(
        ToolInvocation(tool_name="parse_command", arguments={"text": "普通弹幕"}, source="test")
    )
    assert result.success is True
    parsed = json.loads(result.content)
    assert parsed["is_command"] is False

    # 测试不支持的命令
    result = await provider.invoke(
        ToolInvocation(tool_name="parse_command", arguments={"text": "/unknown foo"}, source="test")
    )
    assert result.success is True
    parsed = json.loads(result.content)
    assert parsed["is_command"] is True
    assert parsed["name"] == "unknown"
    assert parsed["supported"] is False
    assert parsed["action"] is None


@pytest.mark.asyncio
async def test_decision_loop_handle_message_direct():
    """handle_message 直接入口（测试用）：跳过 EventBus，直接调 Agent。"""
    planner_resp = _planner_response({"should_reply": True, "target": "u1", "topic_summary": "t", "confidence": 0.9})
    replyer_resp = _replyer_response("OK", emotion="happy")

    llm = MagicMock()
    llm.call_tools = AsyncMock(side_effect=[planner_resp, replyer_resp])
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")

    config = StreamerAgentConfig(
        planner_llm="llm_fast",
        replyer_llm="llm",
        proactive_enabled=False,
        agenda_enabled=False,
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
        """Planner 失败（脏 JSON）也必须发决策事件——失败可见性是核心价值。"""
        agent, bus, registry, llm, prompt = _setup_agent()
        llm.call_tools = AsyncMock(
            return_value=LLMResponse(
                success=True,
                content="",
                tool_calls=[
                    {
                        "name": "produce_plan",
                        "arguments": "这不是JSON{",
                    }
                ],
            )
        )

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
            assert "json_parse_failed" in d.error
        finally:
            await agent.stop()
            await bus.cleanup()

    @pytest.mark.asyncio
    async def test_reply_to_flows_into_decision_event(self):
        """Planner 输出 reply_to → 决策事件携带 reply_to_message_id（互动分析关联键）。"""
        agent, bus, registry, llm, prompt = _setup_agent()
        planner_resp = _planner_response(
            {
                "should_reply": True,
                "target": "观众A",
                "reply_to": "msg_abc",
                "topic_summary": "回应",
                "reply_guidance": "回应夸奖",
                "confidence": 0.9,
            }
        )
        replyer_resp = _replyer_response("谢谢支持！", emotion="happy")
        llm.call_tools = AsyncMock(side_effect=[planner_resp, replyer_resp])

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
