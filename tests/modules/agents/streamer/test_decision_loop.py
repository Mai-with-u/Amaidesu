"""StreamerAgent 决策循环集成测试（Planner ReAct 架构）。

Planner 以 ReAct 循环运行（``llm.generate(messages, tools=..., profile="planner")`` +
全局工具列表 + reply 局部工具）；Replyer 走 ``llm.generate(prompt, tools=[reply],
profile="replyer")``。测试 mock 按 profile 分流：
- Planner LLM 响应 = tool_calls 为中立 ToolCall 对象（arguments 为 dict）
- Replyer LLM 响应 = tool_calls[0] 为 reply ToolCall（arguments 为 JSON 字符串）

决策流：Planner 循环内调 reply（经 _reply_provider.invoke → Replyer.generate）；
自然终止（无 tool_calls）= 静默。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.llm.payload import Response as PayloadResponse
from src.modules.llm.payload import ToolCall as PayloadToolCall
from src.modules.tools import ToolRegistry
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload, StreamerStagePayload


def _make_payload(text: str = "主播好可爱") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众A"),
        content=text,
    )


# ---------------------------------------------------------------------------
# 响应工厂（Planner ReAct / Replyer 均走 generate；tool_calls 为中立 ToolCall）
# ---------------------------------------------------------------------------


def _planner_tool_call(name: str, args: dict, call_id: str = "call_p1") -> PayloadToolCall:
    """构造 Planner 的中立 tool_call（arguments 为 dict）。"""
    return PayloadToolCall(id=call_id, name=name, arguments=args)


def _planner_react_response(tool_calls: list) -> PayloadResponse:
    """构造 Planner 的 generate 响应（空 tool_calls = 自然终止）。"""
    return PayloadResponse(success=True, content="", tool_calls=tool_calls)


def _replyer_response(speech: str, emotion: str = "happy", actions: list | None = None) -> PayloadResponse:
    """构造 Replyer 的 generate 响应（tool_calls[0] = reply）。"""
    tool_calls = [
        PayloadToolCall(
            id="call_reply",
            name="reply",
            arguments={"speech": speech, "emotion": emotion},
        )
    ]
    if actions:
        for action in actions:
            tool_calls.append(
                PayloadToolCall(
                    id="call_" + action["name"],
                    name=action["name"],
                    arguments=action.get("parameters", {}),
                )
            )
    return PayloadResponse(success=True, content="", tool_calls=tool_calls)


def _replyer_failure(reason: str = "mock failure") -> PayloadResponse:
    """构造 Replyer LLM 失败响应（success=False）。"""
    return PayloadResponse(success=False, content=None, error=reason)


def _make_generate_mock(planner_side, replyer_return) -> AsyncMock:
    """generate mock：按 profile 分流——planner 消费 side_effect 队列，replyer 返回固定值。"""

    async def _dispatch(*args, **kwargs):
        if kwargs.get("profile") == "planner":
            if isinstance(planner_side, Exception):
                raise planner_side
            return planner_side.pop(0) if isinstance(planner_side, list) else planner_side
        if isinstance(replyer_return, Exception):
            raise replyer_return
        return replyer_return

    return AsyncMock(side_effect=_dispatch)


def _planner_calls(llm: MagicMock) -> list:
    """generate 调用中 profile=planner 的子集（ReAct 轮数断言用）。"""
    return [c for c in llm.generate.await_args_list if c.kwargs.get("profile") == "planner"]


def _replyer_calls(llm: MagicMock) -> list:
    """generate 调用中 profile=replyer 的子集（Replyer 触发断言用）。"""
    return [c for c in llm.generate.await_args_list if c.kwargs.get("profile") == "replyer"]


# ---------------------------------------------------------------------------
# Agent 装配（Planner ReAct + Replyer 均走 generate）
# ---------------------------------------------------------------------------


def _setup_agent(
    chat_responses: list | None = None,
    config_overrides: dict | None = None,
) -> tuple[StreamerAgent, EventBus, ToolRegistry, MagicMock, MagicMock]:
    """构造完整测试 Agent：mock LLM generate 按 profile 分流（Planner / Replyer）。

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
    llm.generate = _make_generate_mock(list(chat_responses), replyer_resp)

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = StreamerConfig.from_dict(
        {
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
            "proactive": {"enabled": False},
            **(config_overrides or {}),
        }
    )

    bus = EventBus()
    registry = ToolRegistry()

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=bus,
        tool_registry=registry,
    )

    return agent, bus, registry, llm, prompt


@pytest.mark.asyncio
async def test_decision_loop_danmaku_to_reply_provider():
    """决策循环端到端：弹幕事件 → Planner generate（ReAct 调 reply）→ Replyer generate → 发言管线。

    reply 是真工具：注册进 ToolRegistry（名单 [streamer]）；Planner 循环内暂仍经
    _reply_provider.invoke 直连（调用统一在后续任务收口）。proactive/command 是
    代码直连的内部件，不注册——无自身过程与推进权，属被动原语。
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

        # 2. Planner ReAct 至少一轮 generate
        assert len(_planner_calls(llm)) >= 1, "Planner 应至少调一次 generate"

        # 3. 循环内调 reply → Replyer 生成
        assert len(_replyer_calls(llm)) >= 1, "Planner 调 reply 后 Replyer 应被触发"

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
    """Planner 自然终止（无 tool_calls）→ 不触发 Replyer 生成，静默收场。"""
    chat_responses = [_planner_react_response([])]

    llm = MagicMock()
    llm.generate = _make_generate_mock(list(chat_responses), _replyer_response("不应被消费"))

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
        }
    )

    bus = EventBus()
    registry = ToolRegistry()

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
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

        # Planner 恰好 1 轮 generate（自然终止），Replyer 不调
        assert len(_planner_calls(llm)) == 1, "自然终止应恰好 1 轮"
        assert len(_replyer_calls(llm)) == 0, "自然终止时 Replyer 不应被触发"

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
        config_overrides={"proactive": {"enabled": True}},
    )

    await agent.start()
    try:
        agent._rundown_proactive_pending = True

        await asyncio.sleep(0.2)
        assert agent.get_statistics()["total_proactive"] == 0, "未开播时主动发言应静默"
        assert agent._rundown_proactive_pending is True, "pending 信号不应被消费"

        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=1, source="manual", title="测试场"),
            source="test",
        )
        await asyncio.sleep(0.2)

        assert agent._live_active is True
        assert agent.get_statistics()["total_proactive"] >= 1, "开播后首个 tick 应消费保留的 pending 触发主动发言"
        assert agent._rundown_proactive_pending is False
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_proactive_gated_after_live_ended():
    """下播收闸：live.ended 后主动发言静默，弹幕回复不受影响。"""
    agent, bus, registry, llm, prompt = _setup_agent(
        chat_responses=[_planner_react_response([])],
        config_overrides={"proactive": {"enabled": True}},
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
        baseline = agent.get_statistics()["total_proactive"]
        await asyncio.sleep(0.3)
        assert agent.get_statistics()["total_proactive"] == baseline, "下播后主动发言应静默（无新增触发）"
        assert agent._rundown_proactive_pending is True, "下播后的 pending 信号保留待下场"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_decision_loop_danmaku_reply_not_gated_by_live_session() -> None:
    """场次闸只挡主动发言分支：弹幕回复路径不受开播状态影响。"""
    agent, bus, registry, llm, prompt = _setup_agent()

    # 验证的是弹幕是否进入决策，不把机器能否在 200 毫秒内调度完协程当作开播门控。
    planner_started = asyncio.Event()
    dispatch = llm.generate.side_effect

    async def observe_generate(*args: Any, **kwargs: Any) -> PayloadResponse:
        if kwargs.get("profile") == "planner":
            planner_started.set()
        return await dispatch(*args, **kwargs)

    llm.generate.side_effect = observe_generate

    await agent.start()
    try:
        await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("主播好可爱！"), source="bilibili")
        await asyncio.wait_for(planner_started.wait(), timeout=3.0)
        assert len(_planner_calls(llm)) >= 1, "未开播时弹幕回复不应被门控"
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_decision_loop_handle_message_direct():
    """handle_message 直接入口（测试用）：跳过 EventBus，直接调 Agent。"""
    llm = MagicMock()
    llm.generate = _make_generate_mock(_planner_react_response([]), _replyer_response("OK", emotion="happy"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
        }
    )

    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=ToolRegistry(),
    )

    # 不启动（不订阅事件），直接 handle_message
    await agent.handle_message(_make_payload("直接调用"))

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
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload, source="bilibili")
            await asyncio.sleep(0.05)
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
        llm.generate = _make_generate_mock(RuntimeError("boom"), _replyer_response("x"))

        decisions: list = []

        async def _on_decision(name, payload, source):
            decisions.append(payload)

        bus.on(CoreEvents.PLANNER_DECISION, _on_decision, model_class=PlannerDecisionPayload)

        await agent.start()
        try:
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("hi"), source="t")
            await asyncio.sleep(0.05)
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
            await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, _make_payload("主播好可爱"), source="t")
            await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)

            assert len(decisions) == 1
            assert decisions[0].reply_to_message_id == "msg_abc"
        finally:
            await agent.stop()
            await bus.cleanup()


class TestStatisticsFields:
    """统计字段口径验证：reply_duration_ms 实测写回 / replyer_failures 失败递增。"""

    @pytest.mark.asyncio
    async def test_reply_duration_ms_measured_on_successful_reply(self):
        """成功回复轮：reply_duration_ms 由 reply 工具调用实测写回，必须 > 0。"""
        agent, bus, registry, llm, prompt = _setup_agent()

        # time.time 粒度在 Windows 上较粗，mock 秒回时毫秒差值可能取整为 0；
        # replyer 分支注入 50ms 延迟保证实测耗时可靠大于 0。
        inner = llm.generate.side_effect

        async def _slow_dispatch(*args, **kwargs):
            if kwargs.get("profile") == "replyer":
                await asyncio.sleep(0.05)
            return await inner(*args, **kwargs)

        llm.generate = AsyncMock(side_effect=_slow_dispatch)

        await agent.start()
        try:
            result = await agent.debug_test_decision(batch=[{"user": "观众A", "text": "主播好可爱"}])
        finally:
            await agent.cleanup()

        assert result["speech"] == "谢谢支持！"
        assert result["reply_duration_ms"] > 0, (
            f"成功回复轮 reply_duration_ms 应实测 > 0，实际 {result['reply_duration_ms']}"
        )
        assert agent.get_statistics()["replyer_failures"] == 0

    @pytest.mark.asyncio
    async def test_replyer_failure_increments_counter(self):
        """reply 工具失败（Replyer LLM 失败 → ToolExecutionResult.success=False）→ _replyer_failures 递增。"""
        agent, bus, registry, llm, prompt = _setup_agent(
            chat_responses=[
                _planner_react_response(
                    [
                        _planner_tool_call(
                            "streamer_reply", {"topic_summary": "t", "reply_guidance": "g", "target": "u1"}
                        )
                    ]
                ),
                _planner_react_response([]),
            ]
        )
        # replyer 分支返回失败响应（Replyer LLM 失败 → reply 工具失败 → 计数递增）
        inner = llm.generate.side_effect

        async def _failing_dispatch(*args, **kwargs):
            if kwargs.get("profile") == "replyer":
                return _replyer_failure("llm down")
            return await inner(*args, **kwargs)

        llm.generate = AsyncMock(side_effect=_failing_dispatch)

        await agent.start()
        try:
            result = await agent.debug_test_decision(batch=[{"user": "观众A", "text": "hi"}])
        finally:
            await agent.cleanup()

        assert result["speech"] is None
        # mock 秒回时毫秒整型计时可为 0；耗时字段存在且非负即口径成立
        assert result["reply_duration_ms"] >= 0
        stats = agent.get_statistics()
        assert stats["replyer_failures"] == 1
        assert stats["planner_failures"] == 0, "reply 失败不应记入 planner_failures"
        assert stats["total_replies"] == 0
        assert stats["total_no_action"] == 1

    @pytest.mark.asyncio
    async def test_no_reply_round_with_consecutive_replyer_failures_stats_consistent(self):
        """边界：无成功回复轮 + replyer 连续失败 → get_statistics 正常返回且口径自洽。"""
        agent, bus, registry, llm, prompt = _setup_agent(
            chat_responses=[
                _planner_react_response(
                    [
                        _planner_tool_call(
                            "streamer_reply", {"topic_summary": "t", "reply_guidance": "g", "target": "u1"}
                        )
                    ]
                ),
                _planner_react_response(
                    [
                        _planner_tool_call(
                            "streamer_reply", {"topic_summary": "t", "reply_guidance": "g", "target": "u1"}
                        )
                    ]
                ),
                # reply 失败后 Planner 循环内会再取响应重试，补足自然终止响应
                # 防止 round2 StopIteration 被误记为 planner 失败
                _planner_react_response([]),
                _planner_react_response([]),
                _planner_react_response([]),
            ]
        )
        # replyer 分支返回失败响应（Replyer LLM 失败 → reply 工具失败 → 计数递增）
        inner = llm.generate.side_effect

        async def _failing_dispatch(*args, **kwargs):
            if kwargs.get("profile") == "replyer":
                return _replyer_failure("llm down")
            return await inner(*args, **kwargs)

        llm.generate = AsyncMock(side_effect=_failing_dispatch)

        await agent.start()
        try:
            first = await agent.debug_test_decision(batch=[{"user": "观众A", "text": "hi"}])
            second = await agent.debug_test_decision(batch=[{"user": "观众A", "text": "hi2"}])
        finally:
            await agent.cleanup()

        assert first["speech"] is None and second["speech"] is None
        stats = agent.get_statistics()
        assert stats["replyer_failures"] == 2
        assert stats["planner_failures"] == 0
        assert stats["total_replies"] == 0
        assert stats["total_no_action"] == 2
        # 口径自洽：总轮数 = 回复 + 无动作
        assert stats["total_replies"] + stats["total_no_action"] == 2
