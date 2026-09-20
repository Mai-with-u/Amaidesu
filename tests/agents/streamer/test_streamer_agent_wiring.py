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

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.client import LLMResponse
from src.modules.llm.payload import Response
from src.modules.tools import ToolExecutionResult, ToolInvocation
from src.modules.tools.registry import ToolRegistry


def _make_agent_config(**overrides) -> StreamerConfig:
    """构造测试用 StreamerConfig。

    支持顶层覆盖与子段覆盖两种形式，如 ``batch_window_ms``（嵌套 batch 段）
    与 ``word_filter_enabled``（扁平便利字段——测试代码不感知嵌套结构）。
    """
    defaults: Dict[str, Any] = {
        "proactive": {"enabled": False},
        "word_filter": {"enabled": False},
        "batch": {
            "batch_window_ms": 100,
            "tick_interval_ms": 50,
        },
    }
    # 扁平覆盖键（兼容旧测试代码写法）：batch_* 进 batch 子段，
    # word_filter_* 进 word_filter 子段，proactive_* 进 proactive 子段。
    flat_to_nested: Dict[str, tuple[str, str]] = {
        "batch_window_ms": ("batch", "batch_window_ms"),
        "batch_max_size": ("batch", "batch_max_size"),
        "tick_interval_ms": ("batch", "tick_interval_ms"),
        "enable_idle_compensation": ("batch", "enable_idle_compensation"),
        "word_filter_enabled": ("word_filter", "enabled"),
        "profanity_enabled": ("word_filter", "enabled"),
        "word_filter_words": ("word_filter", "words"),
        "profanity_words": ("word_filter", "words"),
        "proactive_enabled": ("proactive", "enabled"),
        "proactive_cold_timeout_ms": ("proactive", "cold_timeout_ms"),
        "rundown_speech_interval_ms": ("proactive", "rundown_speech_interval_ms"),
    }
    for key, value in overrides.items():
        if key in flat_to_nested:
            section, field = flat_to_nested[key]
            defaults.setdefault(section, {})[field] = value
        else:
            defaults[key] = value
    return StreamerConfig.from_dict(defaults)


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
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    llm.chat = AsyncMock()  # 兼容旧调用（不应被实际触发）
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = _make_agent_config()
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
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
        agent._speech.dispatch(payload)

        assert agent._speech.utterance_queue is not None
        for _ in range(50):
            if len(engine.handle_speech_calls) >= 1:
                break
            await asyncio.sleep(0.01)

        stats = agent._speech.utterance_queue.get_stats()
        assert stats["enqueued"] == 1
        assert stats["queue_size"] == 0  # worker 已取走
        assert agent._speech.utterance_seq == 1

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

    agent._speech.utterance_queue.enqueue = _capturing_enqueue  # type: ignore[method-assign]

    try:
        for i in range(3):
            payload = {
                "speech": f"第 {i} 句",
                "emotion": "",
                "actions": [],
                "metadata": {},
            }
            agent._speech.dispatch(payload)

        await asyncio.sleep(0.05)

        assert len(captured_utterance_ids) == 3
        assert agent._speech.utterance_seq == 3

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
        agent._speech.dispatch(["speech", "emotion"])  # list 而非 dict

        await asyncio.sleep(0.05)
        assert agent._speech.utterance_queue.get_stats()["enqueued"] == 0
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
        assert agent._speech.utterance_queue is None
        assert agent._speech.tts_enabled is False

        payload = {
            "speech": "会被忽略",
            "emotion": {"name": "happy", "intensity": 0.5},
            "actions": [{"name": "do_something", "parameters": {}}],
            "metadata": {"x": 1},
        }
        called = {"vts": False}

        def _spy(emotion: str) -> None:
            called["vts"] = True

        agent._speech._schedule_vts_emotion = _spy  # type: ignore[method-assign]
        agent._speech.dispatch(payload)

        await asyncio.sleep(0.05)
        assert called["vts"] is False, "TTS disabled 时不应触发 VTS 调用"
        assert agent._speech.utterance_seq == 1
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_speech_config_none_means_disabled():
    """speech_config=None（默认）→ TTS 关闭。"""
    agent = _build_streamer_agent(tts_engine=None, speech_config=None)
    await agent._on_start()
    try:
        assert agent._speech.tts_enabled is False
        assert agent._speech.utterance_queue is None
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
        assert agent._speech.tts_enabled is False
        assert agent._speech.utterance_queue is None
    finally:
        await agent._on_stop()


@pytest.mark.asyncio
async def test_empty_speech_does_not_enqueue():
    """speech 为空字符串或仅空白 → 不入队、不发业务事件。"""
    engine = _MockTTSEngine()
    agent = _build_streamer_agent(
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
        agent._speech.dispatch(payload)
        await asyncio.sleep(0.2)

        assert agent._speech.utterance_queue.get_stats()["enqueued"] == 0
        assert agent._speech.utterance_seq == 0
        assert len(engine.handle_speech_calls) == 0
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
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")

    config = _make_agent_config(profanity_enabled=False)
    agent = StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=ToolRegistry(),
        speech_config={"enabled": False},
        tts_engine=None,
    )

    # Patch Planner.plan 为 outcome dict（绕开 LLM；ReAct 循环内 reply 已完成）
    agent._planner.plan = AsyncMock(
        return_value={
            "replied": True,
            "target": "u1",
            "topic_summary": "t",
            "reply_guidance": "r",
            "confidence": 0.9,
            "speech": "OK",
            "emotion": "happy",
            "reply_payload": {
                "speech": "OK",
                "emotion": {"name": "happy", "intensity": 0.5},
                "actions": [{"name": "wave", "parameters": {}}],
                "metadata": {},
            },
        }
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
        history_provider=None,
    )

    await agent._rounds.execute(
        batch=[],
        forced=False,
        trigger_reason="test",
    )

    stats = agent.get_statistics()
    assert stats["total_replies"] == 1
    assert agent._speech.utterance_queue is None
