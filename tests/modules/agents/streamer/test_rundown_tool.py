"""RundownControlProvider 单元测试

验证 Agent 内脏协议工具的调用契约：结构化拒绝回灌（unknown id / 未达
最少停留）、成功快照、参数缺失与未知动作的防御。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from src.agents.streamer.rundown.rundown import Rundown, RundownSegment, DEFAULT_RUNDOWN
from src.agents.streamer.rundown.rundown_state import RundownState
from src.agents.streamer.tools.rundown_tool import (
    RundownControlProvider,
    build_rundown_control_function_def,
)


class _FakeClock:
    """可手动拨动的假时钟（毫秒）。"""

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _make_rundown_with_min() -> Rundown:
    """首段带 min_duration 守卫的三段流程单（默认流程单不含 min）。"""
    return Rundown(
        rundown_id="tool_rd",
        title="工具测试流程单",
        segments=[
            RundownSegment(
                id="opening",
                title="开场",
                task_description="开场目标",
                expected_ms=600_000,
                min_duration_ms=60_000,
            ),
            RundownSegment(id="chat", title="闲聊", task_description="闲聊目标", expected_ms=600_000),
            RundownSegment(id="closing", title="收尾", task_description="收尾目标", expected_ms=300_000),
        ],
    )


def _make_provider(clock: _FakeClock, rundown: Rundown) -> RundownControlProvider:
    state = RundownState(clock=clock)
    state.load(rundown, now_ms=clock.now)
    return RundownControlProvider(state)


def _parse(text: str) -> Dict[str, Any]:
    return json.loads(text)


def test_function_def_shape() -> None:
    fn = build_rundown_control_function_def()
    assert fn["name"] == "rundown_control"
    props = fn["parameters"]["properties"]
    assert props["action"]["enum"] == ["next", "goto", "pause", "resume"]
    assert fn["parameters"]["required"] == ["action"]


def test_is_active_reflects_state() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    provider = RundownControlProvider(state)
    assert provider.is_active() is False

    state.load(DEFAULT_RUNDOWN, now_ms=clock.now)
    assert provider.is_active() is True


def test_invoke_next_success_returns_snapshot() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is True
    assert result["rundown"]["status"] == "running"
    assert result["rundown"]["current"]["id"] == "self_intro"


def test_invoke_next_min_duration_rejected_with_remaining() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, _make_rundown_with_min())

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is False
    assert result["reason"] == "min_duration_not_met"
    assert result["remaining_ms"] == 60_000


def test_invoke_goto_requires_segment_id() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "goto"}))

    assert result["ok"] is False
    assert "segment_id" in result["error"]


def test_invoke_goto_unknown_id_returns_available_ids() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "goto", "segment_id": "nope"}))

    assert result["ok"] is False
    assert result["reason"] == "unknown_segment_id"
    assert result["available_segment_ids"] == ["opening", "self_intro", "chat", "closing"]


def test_invoke_pause_and_resume() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    paused = _parse(provider.invoke({"action": "pause"}))
    assert paused["ok"] is True
    assert paused["rundown"]["status"] == "paused"

    resumed = _parse(provider.invoke({"action": "resume"}))
    assert resumed["ok"] is True
    assert resumed["rundown"]["status"] == "running"


def test_invoke_unknown_action_rejected() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)

    result = _parse(provider.invoke({"action": "teleport"}))

    assert result["ok"] is False
    assert "teleport" in result["error"]


def test_invoke_finish_marks_done() -> None:
    clock = _FakeClock()
    provider = _make_provider(clock, DEFAULT_RUNDOWN)
    for _ in range(3):
        provider.invoke({"action": "next"})

    result = _parse(provider.invoke({"action": "next"}))

    assert result["ok"] is True
    assert result["rundown"]["status"] == "done"
