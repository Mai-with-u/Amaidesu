"""StreamerAgent 发言管线接线测试（Y 模型：call_tools + dict structured_content）。

Y 模型契约（v2）：
- ``_dispatch_speech_and_emotion(reply_payload)`` 入参 = ``reply_result.structured_content``（dict）
  而非 JSON 字符串；reply_tool 已把 Replyer 返回 dict 装入 ``ToolExecutionResult.structured_content``。
- LLM 入口 = ``llm.call_tools``（标准 function calling）；Planner/Replyer 各自的 tool_call 名
  = ``produce_plan`` / ``reply``。

覆盖需求：
- speech 非空 + TTS enabled → 入队 + 引擎 handle_speech 收到正确 utterance_id
- 序号 seq 每次递增
- payload 非 dict → 记录 WARN 日志，不入队（structured_content 兜底路径）
- TTS disabled → 既不入队也不调 VTS（utterance_id 仍递增用于业务事件）
- emotion 触发 VTS 工具调用（fire-and-forget via create_task）
- actions 走 ToolRegistry（Y 模型统一接入，不再"未使用"）
- 决策循环在 TTS 关闭时仍正常完成

测试方法：构造最小化 StreamerAgent（mock LLM + mock ToolRegistry +
mock TTS 引擎），直接调用 ``_dispatch_speech_and_emotion`` 验证分发逻辑；
或在更大集成层用 mock reply_provider.invoke 验证 _make_two_stage_decision。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
from src.modules.llm.manager import LLMResponse
from src.modules.tools import ToolExecutionResult, ToolInvocation
from src.modules.tools.registry import ToolRegistry


def _make_agent_config(**overrides) -> StreamerAgentConfig:
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
    tool_registry: Optional[ToolRegistry] = None,
    speech_config: Optional[Dict[str, Any]] = None,
    tts_engine: Optional[Any] = None,
) -> StreamerAgent:
    """构造最小化 StreamerAgent：mock LLM/Prompt/Context/EventBus。

    Y 模型：LLM 入口 = ``call_tools``。本测试多数路径直调 ``_dispatch_speech_and_emotion``
    不触发 LLM，但 mock 仍配置为 ``success=False`` 兜底（防止偶发调用报错）。
    """
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.chat = AsyncMock()  # 兼容旧调用（不应被实际触发）
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    context = MagicMock()
    context.get_history = AsyncMock(return_value=[])

    config = _make_agent_config()
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context,
        event_bus=None,
        tool_registry=tool_registry,
        speech_config=speech_config,
        tts_engine=tts_engine,
    )


class _MockTTSEngine:
    """TTS 引擎 mock：录制所有 handle_speech 调用。"""

    def __init__(self) -> None:
        self.handle_speech_calls: List[tuple[str, Optional[str]]] = []
        self.setup_called = 0

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.handle_speech_calls.append((text, utterance_id))

    async def setup(self) -> None:
        self.setup_called += 1


# ---------------------------------------------------------------------------
# speech 入队：utterance_id 格式 + seq 递增
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speech_enqueued_with_correct_utterance_id_format():
    """speech 非空 + TTS enabled → 入队 payload 含正确格式的 utterance_id。

    编排队列 speak 适配器 → 引擎 handle_speech 端到端验证：
    引擎实例被调用一次，text/utterance_id 正确透传。
    """
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        # Y 模型：structured_content 直接是 dict（reply_tool 已从 Replyer.generate 透传）
        payload = {
            "speech": "谢谢支持！",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(payload)

        assert agent._utterance_queue is not None
        for _ in range(50):
            if len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        stats = agent._utterance_queue.get_stats()
        assert stats["enqueued"] == 1
        assert stats["queue_size"] == 0  # worker 已取走
        assert agent._utterance_seq == 1

        assert len(engine.handle_speech_calls) == 1
        text, uid = engine.handle_speech_calls[0]
        assert text == "谢谢支持！"
        assert uid == "utt_seq_1" or (uid is not None and uid.startswith("utt_"))
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_utterance_seq_increments_per_call():
    """多次 dispatch → seq 递增；utterance_id 格式 ``utt_{ms}_{seq}``。"""
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 5, "render_timeout_ms": 1000},
    )
    await agent._on_start()

    captured_utterance_ids: List[str] = []

    async def _capturing_enqueue(utterance_id: str, text: str) -> bool:
        captured_utterance_ids.append(utterance_id)
        return True

    agent._utterance_queue.enqueue = _capturing_enqueue  # type: ignore[method-assign]

    try:
        for i in range(3):
            payload = {
                "speech": f"第 {i} 句",
                "emotion": "",
                "actions": [],
                "metadata": {},
            }
            agent._dispatch_speech_and_emotion(payload)

        await asyncio.sleep(0.05)

        assert len(captured_utterance_ids) == 3
        assert agent._utterance_seq == 3

        pattern = re.compile(r"^utt_\d+_\d+$")
        for uid in captured_utterance_ids:
            assert pattern.match(uid), f"utterance_id 格式错误: {uid!r}"

        seqs = [int(uid.rsplit("_", 1)[1]) for uid in captured_utterance_ids]
        assert seqs == [1, 2, 3], f"seq 应递增为 [1, 2, 3]，实际 {seqs}"
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# structured_content 非 dict 兜底（Y 模型：消费 dict，不再解析 JSON）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_not_dict_logs_and_skips(loguru_capture):
    """structured_content 非 dict（list/str 等）→ 记录 WARN 日志，不入队。

    Y 模型：``reply_tool.invoke`` 失败或构造异常时可能传入非 dict（防御性兜底）；
    决策循环必须不受影响。
    """
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        agent._dispatch_speech_and_emotion(["speech", "emotion"])  # list 而非 dict

        await asyncio.sleep(0.05)
        assert agent._utterance_queue.get_stats()["enqueued"] == 0
        assert len(engine.handle_speech_calls) == 0
        warn_records = [r for r in loguru_capture.records if r["level"] == "WARNING" and "非 dict" in r["message"]]
        assert warn_records, "应记录非 dict WARN 日志"
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# TTS 关闭路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_disabled_means_no_enqueue_and_no_vts_call():
    """speech_config.enabled=False → 不构造队列、不入队、不调 VTS。

    新增：主播发言业务事件（``streamer.speech``）与 TTS 启用正交——TTS 关闭时仍
    生成 ``utterance_id`` 用于业务事件关联键，但 TTS 入队与 VTS 表情调用都被门控掉。
    """
    agent = _build_streamer_agent(
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": False, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        assert agent._utterance_queue is None
        assert agent._tts_enabled is False

        payload = {
            "speech": "会被忽略",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [{"name": "do_something", "parameters": {}}],
            "metadata": {"x": 1},
        }
        called = {"vts": False}

        def _spy(emotion: str) -> None:
            called["vts"] = True

        agent._schedule_vts_emotion = _spy  # type: ignore[method-assign]
        agent._dispatch_speech_and_emotion(payload)

        await asyncio.sleep(0.05)
        assert called["vts"] is False, "TTS disabled 时不应触发 VTS 调用"
        assert agent._utterance_seq == 1
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_speech_config_none_means_disabled():
    """speech_config=None（默认）→ TTS 关闭。"""
    agent = _build_streamer_agent(tts_engine=None, speech_config=None)
    await agent._on_start()
    try:
        assert agent._tts_enabled is False
        assert agent._utterance_queue is None
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_tts_enabled_but_no_engine_disables_pipeline():
    """TTS enabled 但 tts_engine=None → 降级关闭，队列不构造。"""
    agent = _build_streamer_agent(
        tts_engine=None,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        assert agent._tts_enabled is False
        assert agent._utterance_queue is None
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# emotion → VTS 调用（仍走 ToolRegistry，不变）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emotion_invokes_vts_set_expression_via_create_task():
    """emotion 非空 + TTS enabled → asyncio.create_task 调 vts_set_expression。

    Y 模型：emotion = ``{"name": ..., "intensity": ...}`` dict 形态（replyer 内部
    解包 name 字符串传给 VTS 工具）。
    """
    registry = ToolRegistry()
    vts_invocations: List[ToolInvocation] = []
    invoke_started = asyncio.Event()

    async def _mock_vts_impl(invocation: ToolInvocation) -> ToolExecutionResult:
        vts_invocations.append(invocation)
        invoke_started.set()
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    from src.modules.tools import ToolSpec

    registry.register(
        ToolSpec(
            name="vts_set_expression",
            description="mock vts",
            kind="sync",
            provider="builtin",
            parameters_schema={
                "type": "object",
                "properties": {
                    "parameters": {"type": "object"},
                    "weight": {"type": "number"},
                },
                "required": ["parameters"],
            },
        ),
        _mock_vts_impl,
    )

    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        payload = {
            "speech": "很高兴见到你",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(payload)

        await asyncio.wait_for(invoke_started.wait(), timeout=2.0)

        vts_calls = [inv for inv in vts_invocations if inv.tool_name == "vts_set_expression"]
        assert len(vts_calls) == 1
        call = vts_calls[0]
        assert call.arguments["parameters"] == {"MouthSmile": 1.0}
        # intensity 直接映射为表情混合权重
        assert call.arguments["weight"] == 0.5
        assert call.source == "streamer_agent.emotion"
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_emotion_intensity_maps_to_vts_weight():
    """emotion.intensity 透传为 vts_set_expression 的 weight（含 clamp）。"""
    registry = ToolRegistry()
    vts_invocations: List[ToolInvocation] = []
    invoke_started = asyncio.Event()

    async def _mock_vts_impl(invocation: ToolInvocation) -> ToolExecutionResult:
        vts_invocations.append(invocation)
        invoke_started.set()
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    from src.modules.tools import ToolSpec

    registry.register(
        ToolSpec(
            name="vts_set_expression",
            description="mock vts",
            kind="sync",
            provider="builtin",
            parameters_schema={
                "type": "object",
                "properties": {
                    "parameters": {"type": "object"},
                    "weight": {"type": "number"},
                },
                "required": ["parameters"],
            },
        ),
        _mock_vts_impl,
    )

    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        agent._dispatch_speech_and_emotion(
            {
                "speech": "太兴奋了！！",
                "emotion": {"name": "excited", "intensity": 0.9},
                "actions": [],
                "metadata": {},
            }
        )

        await asyncio.wait_for(invoke_started.wait(), timeout=2.0)

        vts_calls = [inv for inv in vts_invocations if inv.tool_name == "vts_set_expression"]
        assert len(vts_calls) == 1
        assert vts_calls[0].arguments["weight"] == 0.9
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_unknown_emotion_does_not_call_vts():
    """未知 emotion → 不调用 VTS（DEBUG 日志）。"""
    registry = ToolRegistry()
    vts_invocations: List[ToolInvocation] = []

    async def _mock_vts_impl(invocation: ToolInvocation) -> ToolExecutionResult:
        vts_invocations.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    from src.modules.tools import ToolSpec

    registry.register(
        ToolSpec(
            name="vts_set_expression",
            description="mock vts",
            kind="sync",
            provider="builtin",
        ),
        _mock_vts_impl,
    )

    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        payload = {
            "speech": "x",
            "emotion": {"name": "totally_made_up_emotion", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(payload)
        await asyncio.sleep(0.2)
        assert vts_invocations == [], f"未知 emotion 不应触发 VTS 调用，实际: {vts_invocations}"
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_vts_exception_does_not_break_decision_loop():
    """VTS 工具抛异常 → _schedule_vts_emotion 兜底，决策循环不受影响。"""
    registry = ToolRegistry()

    async def _failing_vts(invocation: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("simulated VTS failure")

    from src.modules.tools import ToolSpec

    registry.register(
        ToolSpec(
            name="vts_set_expression",
            description="failing mock",
            kind="sync",
            provider="builtin",
        ),
        _failing_vts,
    )

    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        payload = {
            "speech": "x",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(payload)
        await asyncio.sleep(0.3)
        assert agent._tts_enabled is True
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# 入队 / 不入队 边界
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_speech_does_not_enqueue():
    """speech 为空字符串或仅空白 → 不入队（但 emotion 仍可触发 VTS）。"""
    registry = ToolRegistry()
    vts_calls: List[ToolInvocation] = []

    async def _mock_vts(invocation: ToolInvocation) -> ToolExecutionResult:
        vts_calls.append(invocation)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    from src.modules.tools import ToolSpec

    registry.register(
        ToolSpec(name="vts_set_expression", description="x", kind="sync", provider="builtin"),
        _mock_vts,
    )

    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        payload = {
            "speech": "   ",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [],
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(payload)
        await asyncio.sleep(0.2)

        assert agent._utterance_queue.get_stats()["enqueued"] == 0
        assert agent._utterance_seq == 0
        assert len(engine.handle_speech_calls) == 0
        assert len(vts_calls) == 1
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# 决策循环回归：TTS 关闭时正常完成（Y 模型：call_tools 形态）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_loop_unaffected_when_tts_disabled():
    """TTS disabled → _make_two_stage_decision 仍正常完成（不因新管线报错）。

    Y 模型：Planner 走 ``llm.call_tools(produce_plan)``、Replyer 走 ``llm.call_tools(reply)``
    （经 ``_reply_provider.invoke`` 内部调 ``Replyer.generate``）；本测试绕开 LLM，
    直接 patch ``_planner.plan`` + ``Replyer.generate`` 返回 dict（structured_content 直传）。
    """
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    context = MagicMock()
    context.get_history = AsyncMock(return_value=[])

    config = _make_agent_config(profanity_enabled=False)
    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context,
        event_bus=None,
        tool_registry=ToolRegistry(),
        speech_config={"enabled": False},
        tts_engine=None,
    )

    # Patch Planner.plan 直返 DecisionPlan（绕开 LLM call_tools 失败路径）
    from src.agents.streamer.plan import DecisionPlan

    agent._planner.plan = AsyncMock(
        return_value=DecisionPlan(
            should_reply=True,
            target="u1",
            topic_summary="t",
            reply_guidance="r",
            confidence=0.9,
        )
    )

    from src.agents.streamer.tools.reply_tool import ReplyToolProvider

    replyer = MagicMock()
    replyer.generate = AsyncMock(
        return_value={
            "speech": "OK",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [{"name": "wave", "parameters": {}}],
            "metadata": {},
        }
    )
    agent._reply_provider = ReplyToolProvider(
        replyer=replyer,
        persona={},
        history_provider=None,
        agenda_text_provider=None,
    )

    await agent._make_two_stage_decision(
        batch=[],
        forced=False,
        trigger_reason="test",
    )

    stats = agent.get_statistics()
    assert stats["total_replies"] == 1
    assert agent._utterance_queue is None
