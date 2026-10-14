"""思考流旁路通道：上下文只转发 reasoning 增量，Planner/Replyer 拿到回调，观测失败不反噬 reply"""

from __future__ import annotations

from typing import Any, Dict, List, cast
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import Planner
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.thinking_stream import ThinkingStreamContext
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.llm.payload import Response
from src.modules.tools.models import ToolInvocation

# ThinkingStreamContext


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None:
        self.calls.append({"round_id": round_id, "phase": phase, "step": step, "seq": seq, "text_delta": text_delta})


def test_context_forwards_reasoning_only_with_increasing_seq():
    sink = _RecordingSink()
    ctx = ThinkingStreamContext(sink, round_id="round_1")
    planner_cb = ctx.callback_for("planner", 1)
    planner_cb("reasoning", "想一")
    planner_cb("content", "正文增量")
    planner_cb("reasoning", "")
    planner_cb("reasoning", "想二")
    ctx.callback_for("replyer", 1)("reasoning", "表达侧思考")

    assert [(c["phase"], c["step"], c["seq"], c["text_delta"]) for c in sink.calls] == [
        ("planner", 1, 1, "想一"),
        ("planner", 1, 2, "想二"),
        ("replyer", 1, 3, "表达侧思考"),
    ]
    assert {c["round_id"] for c in sink.calls} == {"round_1"}


# Planner：thinking 透传到 generate


def _make_planner() -> tuple[Planner, Dict[str, Any]]:
    llm = MagicMock()
    captured: Dict[str, Any] = {}

    async def _generate(*args: Any, **kwargs: Any):
        captured.update(kwargs)
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            on_delta("reasoning", "决策中思考")
        # 无 tool_calls → ReAct 自然终止（silent）
        return Response(success=True, content="ok", model="m")

    llm.generate = AsyncMock(side_effect=_generate)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    planner = Planner(
        config={"planner_max_steps": 3},
        llm_service=llm,
        prompt_service=prompt,
        room_state=MagicMock(),
    )
    return planner, captured


async def test_planner_passes_thinking_callback_to_llm():
    planner, captured = _make_planner()
    await planner.plan(batch=[])
    assert captured.get("on_delta") is None

    sink = _RecordingSink()
    outcome = await planner.plan(batch=[], thinking=ThinkingStreamContext(sink, round_id="round_1"))
    assert outcome["silent_reason"] == "natural" and callable(captured["on_delta"])
    assert any(c["text_delta"] == "决策中思考" and c["phase"] == "planner" for c in sink.calls)


# ReplyToolProvider：思考回调一次性槽位


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


# ReplyToolProvider：观测旁路（planner.verdict）异常隔离


async def test_provider_emit_verdict_failure_does_not_block_invoke():
    """观测旁路失败不应反噬调用方：emit 抛异常时 invoke 仍正常返回结果。

    锁定的行为：``_emit_verdict`` 把 ``event_bus.emit`` 放进 ``try/except``；
    即便 ``emit`` 抛 ``RuntimeError``，``invoke`` 主路径（``Replyer.generate``
    调用 + 返回 ``ToolExecutionResult(success=True, structured_content=...)``）
    必须不受影响。覆盖同步转异步 + 删除 ``create_task`` 后的回归面。
    """

    class _Replyer:
        async def generate(self, **kwargs):
            return {"speech": "台词", "llm_request_id": None}

    # 假 event_bus：emit 永远抛 RuntimeError（模拟 emit 派发/订阅侧炸裂）
    failing_bus = MagicMock()
    failing_bus.emit = AsyncMock(side_effect=RuntimeError("emit downstream boom"))

    provider = ReplyToolProvider(
        replyer=cast(Replyer, _Replyer()),
        event_bus=failing_bus,
    )

    result = await provider.invoke(
        ToolInvocation(
            tool_name="streamer_reply",
            arguments={"topic_summary": "s"},
            source="planner-react",
        )
    )

    assert failing_bus.emit.await_count == 1
    assert result.success is True
    assert result.error_message == ""
    # 纯文本契约：Replyer 只出台词，emotion/metadata 由 reply_tool 按 Planner 参数组装
    assert result.structured_content == {
        "speech": "台词",
        "emotion": {"name": "neutral", "intensity": 0.5},
        "metadata": {
            "source_id": "streamer_agent",
            "target": None,
            "topic_summary": "s",
            "reply_guidance": "",
            "confidence": 0.9,
        },
        "llm_request_id": None,
    }
