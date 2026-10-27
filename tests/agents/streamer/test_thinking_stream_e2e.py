"""思考流端到端接线：thinking_sink 注入 → 决策轮构造 ThinkingStreamContext → Planner → LLM on_delta → sink"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.llm.payload import Response
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from tests.agents.streamer.helpers import make_streamer_agent


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "seq": seq, "text_delta": text_delta})


async def _decide(sink: Optional[_RecordingSink], enabled: bool) -> dict:
    async def _generate(*args: Any, **kwargs: Any) -> Response:
        if kwargs.get("on_delta") is not None:
            kwargs["on_delta"]("reasoning", "端到端思考")
        return Response(success=True, content="ok", model="m")

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)
    agent = make_streamer_agent({"thinking_stream": {"enabled": enabled}}, llm=llm, thinking_sink=sink)
    result, _outcome = await agent._rounds._decide_round(
        [],
        round_id="round_e2e",
        started_ms=0,
        forced=False,
        trigger_cause=TriggerCause(kind=TriggerKind.BATCH_FULL),
        proactive=False,
    )
    return result


async def test_decide_round_delivers_reasoning_to_sink() -> None:
    sink = _RecordingSink()
    await _decide(sink, enabled=True)
    assert {"round_id": "round_e2e", "phase": "planner", "seq": 1, "text_delta": "端到端思考"} in sink.calls
    seqs = [c["seq"] for c in sink.calls]
    assert seqs == sorted(seqs)


@pytest.mark.parametrize(("with_sink", "enabled"), [(True, False), (False, True)], ids=["switch_off", "no_sink"])
async def test_thinking_stream_short_circuits(with_sink: bool, enabled: bool) -> None:
    sink = _RecordingSink() if with_sink else None
    result = await _decide(sink, enabled)
    assert result["error"] is None
    assert sink is None or sink.calls == []
