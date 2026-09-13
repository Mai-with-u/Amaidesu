"""思考流旁路通道测试（ADR-008）。

覆盖：
- ThinkingStreamContext：reasoning 转发 + seq 递增 + content 丢弃 + 多阶段
- Planner：thinking 透传到 chat_messages 的 on_delta（每步/每阶段形态正确）
- ReplyToolProvider：思考回调一次性槽位（设置 → invoke 消费 → 清理）
"""

from __future__ import annotations

from typing import Any, Dict, List, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.thinking_stream import ThinkingStreamContext
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.llm.manager import LLMResponse
from src.modules.tools.models import ToolInvocation


# ---------------------------------------------------------------------------
# ThinkingStreamContext
# ---------------------------------------------------------------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append(
            {"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta}
        )


def test_context_forwards_reasoning_with_increasing_seq():
    sink = _RecordingSink()
    ctx = ThinkingStreamContext(sink, round_id="round_1")

    planner_cb = ctx.callback_for("planner", 1)
    planner_cb("reasoning", "想一")
    planner_cb("reasoning", "想二")

    assert [(c["phase"], c["seq"], c["text_delta"]) for c in sink.calls] == [
        ("planner", 1, "想一"),
        ("planner", 2, "想二"),
    ]
    assert all(c["round_id"] == "round_1" for c in sink.calls)


def test_context_drops_content_and_empty_deltas():
    sink = _RecordingSink()
    ctx = ThinkingStreamContext(sink, round_id="round_1")

    cb = ctx.callback_for("planner", 1)
    cb("content", "正文增量")
    cb("reasoning", "")
    cb("reasoning", "思考")

    assert len(sink.calls) == 1
    assert sink.calls[0]["text_delta"] == "思考"


def test_context_replyer_phase_gets_step_1():
    sink = _RecordingSink()
    ctx = ThinkingStreamContext(sink, round_id="round_9")

    ctx.callback_for("replyer", 1)("reasoning", "表达侧思考")

    assert sink.calls[0]["phase"] == "replyer"
    assert sink.calls[0]["step"] == 1
    assert sink.calls[0]["round_id"] == "round_9"


# ---------------------------------------------------------------------------
# Planner：thinking 透传到 chat_messages
# ---------------------------------------------------------------------------


def _make_planner() -> tuple[Planner, Dict[str, Any]]:
    llm = MagicMock()
    captured: Dict[str, Any] = {}

    async def _chat_messages(**kwargs):
        captured.update(kwargs)
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            on_delta("reasoning", "决策中思考")
        # 无 tool_calls → ReAct 自然终止（silent）
        return LLMResponse(success=True, content="ok", model="m")

    llm.chat_messages = AsyncMock(side_effect=_chat_messages)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    planner = Planner(
        config={"planner_llm": "llm_fast", "planner_max_steps": 3},
        llm_service=llm,
        prompt_service=prompt,
        room_state=MagicMock(),
    )
    return planner, captured


@pytest.mark.asyncio
async def test_planner_passes_thinking_callback_to_llm():
    planner, captured = _make_planner()
    sink = _RecordingSink()
    thinking = ThinkingStreamContext(sink, round_id="round_1")

    outcome = await planner.plan(batch=[], thinking=thinking)

    assert outcome["silent_reason"] == "natural"
    assert callable(captured.get("on_delta"))
    # LLM mock 内触发的 reasoning 增量经 context 组装到达 sink
    assert any(c["text_delta"] == "决策中思考" and c["phase"] == "planner" for c in sink.calls)


@pytest.mark.asyncio
async def test_planner_without_thinking_passes_none():
    planner, captured = _make_planner()

    await planner.plan(batch=[])

    assert captured.get("on_delta") is None


# ---------------------------------------------------------------------------
# ReplyToolProvider：思考回调一次性槽位
# ---------------------------------------------------------------------------


def _make_provider_with_replyer_capture() -> tuple[ReplyToolProvider, Dict[str, Any]]:
    captured: Dict[str, Any] = {}

    class _Replyer:
        async def generate(self, **kwargs):
            captured.update(kwargs)
            if kwargs.get("on_delta") is not None:
                kwargs["on_delta"]("reasoning", "表达侧思考")
            return {"speech": "台词", "emotion": {"name": "neutral", "intensity": 0.5}, "actions": []}

    provider = ReplyToolProvider(
        replyer=cast(Replyer, _Replyer()),
    )
    return provider, captured


@pytest.mark.asyncio
async def test_provider_sets_and_clears_thinking_callback():
    provider, captured = _make_provider_with_replyer_capture()
    sink = _RecordingSink()
    ctx = ThinkingStreamContext(sink, round_id="round_1")
    callback = ctx.callback_for("replyer", 1)

    provider.set_thinking_callback(callback)
    result = await provider.invoke(
        ToolInvocation(
            tool_name="streamer_reply",
            arguments={"topic_summary": "s"},
            source="planner-react",
        )
    )

    assert result.success is True
    assert captured["on_delta"] is callback
    assert any(c["text_delta"] == "表达侧思考" for c in sink.calls)
    # 一次性槽位：invoke 后清理
    assert provider._thinking_callback is None
