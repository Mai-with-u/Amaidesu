"""StreamerAgent 思考流端到端接线测试（ADR-008）。

链路：thinking_sink 注入 → _decide_round 构造 ThinkingStreamContext →
Planner.plan → LLM on_delta → sink 收到组装后的增量。
同时验证开关关闭 / sink 缺失时的短路行为。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.manager import LLMResponse


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta})


def _build_agent(thinking_sink: Optional[Any], enabled: bool = True) -> StreamerAgent:
    llm = MagicMock()

    async def _chat_messages(**kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            on_delta("reasoning", "端到端思考")
        return LLMResponse(success=True, content="ok", model="m")

    llm.chat_messages = AsyncMock(side_effect=_chat_messages)
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="PROMPT")
    context = MagicMock()
    context.get_history = AsyncMock(return_value=[])

    config = StreamerConfig.from_dict(
        {
            "proactive": {"enabled": False},
            "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
            "thinking_stream": {"enabled": enabled},
        }
    )
    return StreamerAgent(
        config=config,
        llm_manager=llm,
        prompt_manager=prompt,
        context_service=context,
        event_bus=None,
        thinking_sink=thinking_sink,
    )


@pytest.mark.asyncio
async def test_decide_round_delivers_reasoning_to_sink():
    sink = _RecordingSink()
    agent = _build_agent(thinking_sink=sink)

    await agent._decide_round(
        [],
        round_id="round_e2e",
        started_ms=0,
        forced=False,
        trigger_reason="test",
        proactive=False,
    )

    assert any(
        c["round_id"] == "round_e2e" and c["phase"] == "planner" and c["text_delta"] == "端到端思考" for c in sink.calls
    )
    # seq 轮内递增
    seqs = [c["seq"] for c in sink.calls if c["round_id"] == "round_e2e"]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_disabled_switch_short_circuits_sink():
    sink = _RecordingSink()
    agent = _build_agent(thinking_sink=sink, enabled=False)

    await agent._decide_round(
        [],
        round_id="round_off",
        started_ms=0,
        forced=False,
        trigger_reason="test",
        proactive=False,
    )

    assert sink.calls == []


@pytest.mark.asyncio
async def test_no_sink_means_no_crash():
    agent = _build_agent(thinking_sink=None)

    result = await agent._decide_round(
        [],
        round_id="round_none",
        started_ms=0,
        forced=False,
        trigger_reason="test",
        proactive=False,
    )

    assert result["error"] is None
