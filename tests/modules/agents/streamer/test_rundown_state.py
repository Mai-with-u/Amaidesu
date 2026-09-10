"""RundownState 状态机单元测试

可注入假时钟驱动全部时间场景，不依赖 ``time.sleep``；emit / on_changed
用捕获列表记录调用，验证唯一变更边界的发射与通知契约。
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from src.agents.streamer.rundown.rundown import Rundown, RundownSegment
from src.agents.streamer.rundown.rundown_state import RundownReject, RundownState


# ---------------------------------------------------------------------------
# 测试素材
# ---------------------------------------------------------------------------


class _FakeClock:
    """可手动拨动的假时钟（毫秒）。"""

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _make_rundown() -> Rundown:
    """三段流程单：opening 带 min_duration 守卫，chat / closing 无。"""
    return Rundown(
        rundown_id="test_rd",
        title="测试流程单",
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


def _make_state(
    clock: _FakeClock,
    events: List[Tuple[str, Any]],
    changed: List[Tuple[str, str]],
) -> RundownState:
    """构造带捕获回调的状态机。"""
    return RundownState(
        emit=lambda name, payload: events.append((name, payload)),
        on_changed=lambda segment_id, by: changed.append((segment_id, by)),
        clock=clock,
    )


def _drive_to_last(state: RundownState, clock: _FakeClock) -> None:
    """推进到末段（绕过 opening 的 min_duration：human 免检）。"""
    clock.advance(60_001)
    assert state.next(by="human") is None
    assert state.next(by="human") is None
    assert state.current_segment_id == "closing"


# ---------------------------------------------------------------------------
# 派生状态与 load
# ---------------------------------------------------------------------------


def test_initial_status_is_idle() -> None:
    state = RundownState()
    assert state.status == "idle"
    snapshot = state.get_snapshot()
    assert snapshot["rundown_id"] == ""
    assert snapshot["status"] == "idle"


def test_load_resets_and_emits() -> None:
    clock = _FakeClock()
    events: List[Tuple[str, Any]] = []
    changed: List[Tuple[str, str]] = []
    state = _make_state(clock, events, changed)

    state.load(_make_rundown(), now_ms=clock.now)

    assert state.status == "running"
    assert state.current_segment_id == "opening"
    assert state.rundown_started_at_ms == clock.now
    assert len(events) == 1
    name, payload = events[0]
    assert name == "rundown.changed"
    assert payload.segment_id == "opening"
    assert payload.index == 0
    assert payload.total == 3
    assert payload.by == "system"
    assert changed == [("opening", "system")]


def test_load_clears_previous_pause_accumulation() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    state.pause(by="human", now_ms=clock.now)

    state.load(_make_rundown(), now_ms=clock.now)

    assert state.paused_at_ms is None
    assert state.get_current_remaining_ms(now_ms=clock.now) == 600_000


# ---------------------------------------------------------------------------
# goto
# ---------------------------------------------------------------------------


def test_goto_happy_path_emits_and_notifies() -> None:
    clock = _FakeClock()
    events: List[Tuple[str, Any]] = []
    changed: List[Tuple[str, str]] = []
    state = _make_state(clock, events, changed)
    state.load(_make_rundown(), now_ms=clock.now)
    events.clear()
    changed.clear()
    clock.advance(60_001)

    assert state.goto("chat", by="agent", now_ms=clock.now) is None

    assert state.index == 1
    assert state.current_segment_id == "chat"
    assert events[-1][1].segment_id == "chat"
    assert events[-1][1].by == "agent"
    assert changed == [("chat", "agent")]


def test_goto_unknown_id_rejected_without_mutation() -> None:
    clock = _FakeClock()
    events: List[Tuple[str, Any]] = []
    state = _make_state(clock, events, [])
    state.load(_make_rundown(), now_ms=clock.now)
    events.clear()

    reject = state.goto("nope", by="agent", now_ms=clock.now)

    assert isinstance(reject, RundownReject)
    assert reject.reason == "unknown_segment_id"
    assert reject.available_ids == ["opening", "chat", "closing"]
    assert state.index == 0
    assert events == []


def test_goto_within_min_duration_rejected_for_agent_only() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    agent_reject = state.goto("chat", by="agent", now_ms=clock.now)
    assert isinstance(agent_reject, RundownReject)
    assert agent_reject.reason == "min_duration_not_met"
    assert agent_reject.remaining_ms == 60_000
    assert state.index == 0

    human_reject = state.goto("chat", by="human", now_ms=clock.now)
    assert human_reject is None
    assert state.index == 1


def test_goto_with_invalid_by_raises() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    with pytest.raises(ValueError):
        state.goto("chat", by="robot", now_ms=clock.now)


def test_goto_without_rundown_rejected() -> None:
    state = RundownState()
    reject = state.goto("opening", by="human")
    assert reject is not None
    assert reject.reason == "no_rundown_loaded"


# ---------------------------------------------------------------------------
# next 与 finish
# ---------------------------------------------------------------------------


def test_next_advances_and_finish_marks_done() -> None:
    clock = _FakeClock()
    events: List[Tuple[str, Any]] = []
    changed: List[Tuple[str, str]] = []
    state = _make_state(clock, events, changed)
    state.load(_make_rundown(), now_ms=clock.now)
    _drive_to_last(state, clock)
    events.clear()

    assert state.next(by="agent", now_ms=clock.now) is None

    assert state.status == "done"
    assert state.index == 3
    assert state.current_segment_id == ""
    finish_payload = events[-1][1]
    assert finish_payload.segment_id == ""
    assert finish_payload.index == 3
    assert finish_payload.total == 3
    assert changed[-1] == ("", "agent")


def test_next_after_done_rejected() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    _drive_to_last(state, clock)
    state.next(by="human", now_ms=clock.now)

    reject = state.next(by="human", now_ms=clock.now)

    assert isinstance(reject, RundownReject)
    assert reject.reason == "already_done"


def test_next_within_min_duration_rejected_for_agent_only() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    agent_reject = state.next(by="agent", now_ms=clock.now)
    assert isinstance(agent_reject, RundownReject)
    assert agent_reject.reason == "min_duration_not_met"

    assert state.next(by="human", now_ms=clock.now) is None
    assert state.current_segment_id == "chat"


def test_next_after_min_duration_met_advances() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    clock.advance(60_000)

    assert state.next(by="agent", now_ms=clock.now) is None
    assert state.current_segment_id == "chat"


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


def test_pause_freezes_elapsed_then_resume_accumulates() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    clock.advance(100_000)
    assert state.pause(by="human", now_ms=clock.now) is None
    assert state.status == "paused"

    clock.advance(200_000)
    frozen_remaining = state.get_current_remaining_ms(now_ms=clock.now)
    assert frozen_remaining == 500_000

    assert state.resume(by="human", now_ms=clock.now) is None
    assert state.status == "running"
    clock.advance(50_000)
    assert state.get_current_remaining_ms(now_ms=clock.now) == 450_000


def test_pause_twice_rejected_and_resume_without_pause_rejected() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    assert state.pause(by="human", now_ms=clock.now) is None
    second = state.pause(by="human", now_ms=clock.now)
    assert isinstance(second, RundownReject)
    assert second.reason == "not_running"

    assert state.resume(by="human", now_ms=clock.now) is None
    again = state.resume(by="human", now_ms=clock.now)
    assert isinstance(again, RundownReject)
    assert again.reason == "not_paused"


def test_pause_before_load_rejected() -> None:
    state = RundownState()
    reject = state.pause(by="human")
    assert reject is not None
    assert reject.reason == "not_running"


def test_pause_resume_invalid_by_raises() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    with pytest.raises(ValueError):
        state.pause(by="robot", now_ms=clock.now)
    with pytest.raises(ValueError):
        state.resume(by="robot", now_ms=clock.now)


# ---------------------------------------------------------------------------
# 只读派生
# ---------------------------------------------------------------------------


def test_progress_percent_clamped() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    early = state.get_progress_percent(now_ms=clock.now)
    assert early is not None and early >= 0.0

    clock.advance(10_000_000_000)
    assert state.get_progress_percent(now_ms=clock.now) == 100.0


def test_progress_percent_none_before_load() -> None:
    assert RundownState().get_progress_percent() is None


def test_remaining_zero_when_no_current_segment() -> None:
    assert RundownState().get_current_remaining_ms() == 0


def test_snapshot_shape() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    clock.advance(60_000)

    snapshot = state.get_snapshot(now_ms=clock.now)

    assert snapshot["status"] == "running"
    assert snapshot["rundown_id"] == "test_rd"
    assert snapshot["title"] == "测试流程单"
    assert snapshot["index"] == 0
    assert snapshot["total"] == 3
    assert snapshot["paused"] is False
    current = snapshot["current"]
    assert current is not None
    assert current["id"] == "opening"
    assert current["elapsed_ms"] == 60_000
    assert current["remaining_ms"] == 540_000


# ---------------------------------------------------------------------------
# 变更边界契约：transitions / fail-soft
# ---------------------------------------------------------------------------


def test_transitions_buffer_capped_at_maxlen() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    for _ in range(60):
        assert state.goto("chat", by="human", now_ms=clock.now) is None

    transitions = state.get_transitions()
    assert len(transitions) == 50
    assert transitions[-1]["action"] == "goto"


def test_fail_soft_without_emit_and_on_changed() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)

    state.load(_make_rundown(), now_ms=clock.now)
    assert state.goto("chat", by="human", now_ms=clock.now) is None
    assert state.index == 1


def test_emit_exception_does_not_block_state_change() -> None:
    clock = _FakeClock()

    def _boom(name: str, payload: Any) -> None:
        raise RuntimeError("bus down")

    state = RundownState(emit=_boom, clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)

    assert state.goto("chat", by="human", now_ms=clock.now) is None
    assert state.index == 1


# ---------------------------------------------------------------------------
# 情境注入与整场时长
# ---------------------------------------------------------------------------


def test_build_context_text_and_elapsed_none_before_load() -> None:
    state = RundownState()
    assert state.build_context_text() is None
    assert state.get_elapsed_live_ms() is None


def test_build_context_text_renders_segment_fields() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    rundown = Rundown(
        rundown_id="ctx_rd",
        title="上下文流程单",
        segments=[
            RundownSegment(
                id="opening",
                title="开场",
                task_description="开场目标",
                key_points=["问好", "预告"],
                notes="备注内容",
                expected_ms=600_000,
            ),
            RundownSegment(id="chat", title="闲聊", task_description="闲聊目标", expected_ms=600_000),
        ],
    )
    state.load(rundown, now_ms=clock.now)
    clock.advance(60_000)

    text = state.build_context_text(now_ms=clock.now)

    assert text is not None
    assert "[流程单] 环节 1/2：开场" in text
    assert "剩 约 9 分钟" in text
    assert "目标：开场目标" in text
    assert "要点：问好、预告" in text
    assert "备注：备注内容" in text
    assert "后续环节：闲聊(chat)" in text
    assert "rundown_control" in text


def test_build_context_text_none_when_done() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    _drive_to_last(state, clock)
    state.next(by="human", now_ms=clock.now)

    assert state.build_context_text(now_ms=clock.now) is None


def test_get_elapsed_live_ms_advances_with_clock() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)
    state.load(_make_rundown(), now_ms=clock.now)
    clock.advance(120_000)

    assert state.get_elapsed_live_ms() == 120_000
