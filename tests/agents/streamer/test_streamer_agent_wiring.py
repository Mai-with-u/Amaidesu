"""StreamerAgent 发言管线接线测试。

覆盖需求：
- reply result 含 speech + TTS enabled → 队列收到正确 utterance_id
- 序号 seq 每次递增
- malformed JSON → 记录 WARN 日志，不入队
- TTS disabled → 既不入队也不调 VTS
- emotion 触发 VTS 工具调用（fire-and-forget via create_task）
- action 字段未使用
- 决策循环在 TTS 关闭 / JSON 失败时仍正常完成

测试方法：构造最小化 StreamerAgent（mock LLM + mock ToolRegistry +
mock TTS 引擎），直接调用 ``_dispatch_speech_and_emotion`` 验证分发逻辑；
或在更大集成层用 mock reply_provider.invoke 验证 _make_two_stage_decision。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.streamer_agent import StreamerAgent, StreamerAgentConfig
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
    """构造最小化 StreamerAgent：mock LLM/Prompt/Context/EventBus。"""
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(success=False, content=""))
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
        payload = {
            "speech": "谢谢支持！",
            "emotion": "",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload, ensure_ascii=False))

        # utterance_queue 应已被注入 utterance
        assert agent._utterance_queue is not None
        # 等 worker 取走 + speak 调用
        for _ in range(50):
            if len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        stats = agent._utterance_queue.get_stats()
        assert stats["enqueued"] == 1
        assert stats["queue_size"] == 0  # worker 已取走
        assert agent._utterance_seq == 1

        # 引擎收到正确的 text/utterance_id
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

    # 替换队列入队方法以捕获 utterance_id（无需依赖真实 invoke）
    agent._utterance_queue.enqueue = _capturing_enqueue  # type: ignore[method-assign]

    try:
        for i in range(3):
            payload = {"speech": f"第 {i} 句", "emotion": "", "action": "", "metadata": {}}
            agent._dispatch_speech_and_emotion(json.dumps(payload))

        # 让 fire-and-forget 任务跑起来
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
# malformed JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_logs_and_skips_enqueue(loguru_capture):
    """JSON 解析失败 → 记录 WARN 日志，不入队。"""
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        agent._dispatch_speech_and_emotion("not valid json {{")

        await asyncio.sleep(0.05)

        assert agent._utterance_queue.get_stats()["enqueued"] == 0
        assert len(engine.handle_speech_calls) == 0
        warn_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "WARNING" and "JSON 解析失败" in r["message"]
        ]
        assert warn_records, (
            f"应记录 JSON 解析失败 WARN 日志，实际: "
            f"{[r['message'] for r in loguru_capture.records]}"
        )
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_payload_not_dict_logs_and_skips(loguru_capture):
    """payload 非 dict（如 JSON 数组）→ 记录 WARN 日志，不入队。"""
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
        tts_engine=engine,
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        agent._dispatch_speech_and_emotion(json.dumps(["speech", "emotion"]))

        await asyncio.sleep(0.05)
        assert agent._utterance_queue.get_stats()["enqueued"] == 0
        assert len(engine.handle_speech_calls) == 0
        warn_records = [
            r
            for r in loguru_capture.records
            if r["level"] == "WARNING" and "非 dict" in r["message"]
        ]
        assert warn_records, "应记录非 dict WARN 日志"
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# TTS 关闭路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_disabled_means_no_enqueue_and_no_vts_call():
    """speech_config.enabled=False → 不构造队列、不入队、不调 VTS。"""
    agent = _build_streamer_agent(
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": False, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        assert agent._utterance_queue is None
        assert agent._tts_enabled is False

        # 即使有 speech + emotion 也不应触发
        payload = {
            "speech": "会被忽略",
            "emotion": "happy",
            "action": "do_something",
            "metadata": {"x": 1},
        }
        # monkeypatch _schedule_vts_emotion：若被调用则说明测试断言有问题
        called = {"vts": False}

        def _spy(emotion: str) -> None:
            called["vts"] = True

        agent._schedule_vts_emotion = _spy  # type: ignore[method-assign]
        agent._dispatch_speech_and_emotion(json.dumps(payload))

        await asyncio.sleep(0.05)
        assert called["vts"] is False, "TTS disabled 时不应触发 VTS 调用"
        # seq 不递增（没有任何 utterance_id 被生成）
        assert agent._utterance_seq == 0
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
        # 启动期应已检测到缺失并降级
        assert agent._tts_enabled is False
        assert agent._utterance_queue is None
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# emotion → VTS 调用（仍走 ToolRegistry，不变）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emotion_invokes_vts_set_expression_via_create_task():
    """emotion 非空 + TTS enabled → asyncio.create_task 调 vts_set_expression。"""
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
        # 同时给 speech（避免 dispatch 提前 return）和 emotion
        payload = {
            "speech": "很高兴见到你",
            "emotion": "happy",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload))

        # 等 fire-and-forget 任务执行 invoke
        await asyncio.wait_for(invoke_started.wait(), timeout=2.0)

        # 验证：invoke 被调用过一次，工具名 + 参数形状正确
        vts_calls = [inv for inv in vts_invocations if inv.tool_name == "vts_set_expression"]
        assert len(vts_calls) == 1
        call = vts_calls[0]
        assert call.arguments["parameters"] == {"MouthSmile": 1.0}
        assert call.arguments["weight"] == 1.0
        assert call.source == "streamer_agent.emotion"
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
        # 未知 emotion（如 "totally_made_up_emotion"）不应触发 VTS 调用
        payload = {
            "speech": "x",
            "emotion": "totally_made_up_emotion",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload))
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
        # 触发 emotion 派发（VTS 工具会失败）
        payload = {
            "speech": "x",
            "emotion": "happy",
            "action": "",
            "metadata": {},
        }
        # 不应抛异常
        agent._dispatch_speech_and_emotion(json.dumps(payload))
        # 给 fire-and-forget 任务时间失败
        await asyncio.sleep(0.3)
        # 决策循环应仍正常（agent 状态未受损）
        assert agent._tts_enabled is True
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# action 字段未使用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_field_is_unused():
    """action 字段不影响派发路径（仅 speech + emotion 决定下游行为）。"""
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

    agent = _build_streamer_agent(
        tool_registry=registry,
        tts_engine=_MockTTSEngine(),
        speech_config={"enabled": True, "max_queue": 3, "render_timeout_ms": 1000},
    )
    await agent._on_start()
    try:
        # 带 action 的 payload：action 不应触发任何额外调用
        payload = {
            "speech": "x",
            "emotion": "happy",
            "action": "wave_hand",  # 不应被消费
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload))
        await asyncio.sleep(0.2)
        # 只有 VTS emotion 调用，不应有 "wave_hand" 或类似 action 工具调用
        assert all(
            inv.tool_name != "wave_hand" for inv in vts_calls
        ), "action 字段不应触发任何额外工具调用"
        # 只有 1 次 vts_set_expression
        vts_only = [inv for inv in vts_calls if inv.tool_name == "vts_set_expression"]
        assert len(vts_only) == 1
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
            "speech": "   ",  # 仅空白
            "emotion": "happy",
            "action": "",
            "metadata": {},
        }
        agent._dispatch_speech_and_emotion(json.dumps(payload))
        await asyncio.sleep(0.2)

        # speech 不入队 → stats.enqueued == 0，seq 不递增
        assert agent._utterance_queue.get_stats()["enqueued"] == 0
        assert agent._utterance_seq == 0
        assert len(engine.handle_speech_calls) == 0
        # emotion 仍可触发 VTS（speech 与 emotion 派发互不影响）
        assert len(vts_calls) == 1
    finally:
        await agent._on_stop()


# ---------------------------------------------------------------------------
# 决策循环回归：TTS 关闭 / JSON 失败时正常完成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_loop_unaffected_when_tts_disabled():
    """TTS disabled → _make_two_stage_decision 仍正常完成（不因新管线报错）。"""
    # Planner 决定 should_reply=true + reply 工具返回 JSON content
    planner_json = json.dumps(
        {
            "should_reply": True,
            "target": "u1",
            "topic_summary": "t",
            "reply_guidance": "r",
            "confidence": 0.9,
        }
    )
    replyer_json = json.dumps(
        {
            "text": "OK",
            "emotion": "happy",
            "action": "wave",
            "action_parameters": {},
        },
        ensure_ascii=False,
    )

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[
        MagicMock(success=True, content=planner_json),
        MagicMock(success=True, content=replyer_json),
    ])
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
        speech_config={"enabled": False},  # 关闭
        tts_engine=None,
    )

    # 不 start Agent（手动调用决策循环）
    from src.agents.streamer.tools.reply_tool import ReplyToolProvider
    from src.agents.streamer.replyer import Replyer

    # 构造真实 reply provider（通过 replyer mock）
    replyer = MagicMock()
    replyer.generate = AsyncMock(return_value={
        "text": "OK",
        "emotion": "happy",
        "action": "wave",
        "action_parameters": {},
    })
    agent._reply_provider = ReplyToolProvider(
        replyer=replyer,
        persona={},
        history_provider=None,
        agenda_text_provider=None,
    )

    from src.agents.streamer.plan import DecisionPlan

    plan = DecisionPlan(
        should_reply=True,
        target="u1",
        topic_summary="t",
        reply_guidance="r",
        confidence=0.9,
    )

    # 直接调 _make_two_stage_decision（绕开事件循环订阅）
    await agent._make_two_stage_decision(
        batch=[],
        forced=False,
        trigger_reason="test",
    )

    # 决策循环正常完成：total_replies 增加，无异常
    stats = agent.get_statistics()
    assert stats["total_replies"] == 1
    # TTS 关闭 → 队列不应存在
    assert agent._utterance_queue is None
