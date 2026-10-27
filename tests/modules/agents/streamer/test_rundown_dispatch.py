"""流程单控制动作统一分发器：两条通道共享的分发、参数校验与结构化拒绝。

断言收敛在分发器 interface 上——同一动作无论 by="human" 还是 by="agent"，
参数校验强度与状态机拒绝一致；通道各自的文案翻译由 presentation /
rundown_tool 的既有测试覆盖。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.streamer.rundown.dispatch import (
    APPEND_MIN_EXPECTED_MS,
    INSERT_DEFAULT_EXPECTED_MS,
    dispatch_rundown_control,
)
from src.agents.streamer.rundown.rundown_state import RundownState
from src.modules.storage.models.rundown import DEFAULT_RUNDOWN, Rundown, RundownSegment


def _loaded_state() -> RundownState:
    """已起算（running）的状态：load 只装载结构，分发用例需计时语义故显式 begin。"""
    state = RundownState()
    state.load(DEFAULT_RUNDOWN)
    state.begin(by="system")
    return state


def test_pause_resume_next_share_one_dispatch_regardless_of_actor() -> None:
    for by in ("human", "agent"):
        state = _loaded_state()
        assert dispatch_rundown_control(state, "pause", by=by).success is True
        assert state.status == "paused"
        assert dispatch_rundown_control(state, "resume", by=by).success is True
        outcome = dispatch_rundown_control(state, "next", by=by)
        assert outcome.success is True and outcome.reject is None
        assert state.get_transitions()[-1]["by"] == by


def test_unknown_action_returns_stable_code_with_the_raw_name() -> None:
    outcome = dispatch_rundown_control(_loaded_state(), "rewind", by="human")
    assert (outcome.success, outcome.error_code, outcome.error_detail) == (False, "unknown_action", "rewind")
    assert outcome.reject is None


def test_goto_requires_segment_id_and_reports_unknown_id_via_state_reject() -> None:
    state = _loaded_state()
    missing = dispatch_rundown_control(state, "goto", by="agent")
    assert (missing.success, missing.error_code) == (False, "missing_segment_id")
    # 参数问题不动状态机，也没有结构化拒绝
    assert missing.reject is None and state.status == "running"

    outcome = dispatch_rundown_control(state, "goto", by="agent", segment_id="nope")
    assert outcome.success is False
    assert outcome.reject is not None and outcome.reject.reason == "unknown_segment_id"
    assert outcome.reject.available_ids == [seg.id for seg in DEFAULT_RUNDOWN.segments]


def test_insert_builds_one_off_segment_with_server_generated_id() -> None:
    state = _loaded_state()
    outcome = dispatch_rundown_control(
        state, "insert", by="human", payload={"title": "应急话题", "content": "聊聊趣事"}
    )
    assert outcome.success is True
    inserted = state.rundown.segments[1]
    assert inserted.title == "应急话题"
    assert inserted.task_description == "聊聊趣事"
    assert inserted.id.startswith("insert-")
    assert inserted.expected_ms == INSERT_DEFAULT_EXPECTED_MS


@pytest.mark.parametrize("payload", [None, {}, {"title": "   "}, "不是字典"])
def test_insert_without_blank_free_title_is_param_rejected(payload: Any) -> None:
    state = _loaded_state()
    outcome = dispatch_rundown_control(state, "insert", by="human", payload=payload)
    assert (outcome.success, outcome.error_code) == (False, "missing_insert_title")
    assert outcome.reject is None
    assert len(state.rundown.segments) == 4  # 拒绝不变更


def test_insert_by_agent_hits_state_actor_whitelist_as_value_error() -> None:
    """insert 是导演应急权：分发器原样透传 by，白名单越权由状态机抛 ValueError。"""
    with pytest.raises(ValueError, match="insert_after_current"):
        dispatch_rundown_control(_loaded_state(), "insert", by="agent", payload={"title": "话题"})


def test_append_unified_validation_covers_every_actor() -> None:
    """append 的 expected_ms 下界此前只在 agent 工具侧校验，现收敛为分发器契约。"""
    state = _loaded_state()
    below = dispatch_rundown_control(
        state,
        "append",
        by="agent",
        payload={"seg_id": "tiny", "title": "太短", "task_description": "x", "expected_ms": 30_000},
    )
    assert (below.success, below.error_code) == (False, "append_expected_ms_below_min")
    assert below.error_detail == repr(30_000)
    assert len(state.rundown.segments) == 4  # 拒绝不变更

    missing = dispatch_rundown_control(state, "append", by="agent", payload=None)
    assert (missing.success, missing.error_code) == (False, "append_segment_not_object")

    invalid = dispatch_rundown_control(
        state,
        "append",
        by="agent",
        payload={"seg_id": "", "title": "  ", "task_description": "x", "expected_ms": 120_000},
    )
    assert (invalid.success, invalid.error_code) == (False, "append_segment_invalid")
    assert invalid.error_detail  # 模型校验报错原文进 detail

    ok = dispatch_rundown_control(
        state,
        "append",
        by="agent",
        payload={"seg_id": "bonus", "title": "临时加场", "task_description": "热点", "expected_ms": 120_000},
    )
    assert ok.success is True
    assert state.rundown.segments[-1].id == "bonus"
    assert APPEND_MIN_EXPECTED_MS == 60_000


def test_append_by_human_hits_state_actor_whitelist_as_value_error() -> None:
    """append 是 Agent 的计划扩展权：human 传参再合法也越权，由状态机白名单拦。"""
    segment_payload = {"seg_id": "bonus", "title": "x", "task_description": "x", "expected_ms": 120_000}
    with pytest.raises(ValueError, match="append_segment"):
        dispatch_rundown_control(_loaded_state(), "append", by="human", payload=segment_payload)


def test_min_duration_guard_only_binds_agent_through_the_shared_dispatch() -> None:
    rundown = Rundown(
        rundown_id="guard_rd",
        title="守卫流程单",
        segments=[
            RundownSegment(
                id="opening", title="开场", task_description="目标", expected_ms=600_000, min_duration_ms=60_000
            ),
            RundownSegment(id="chat", title="闲聊", task_description="目标", expected_ms=600_000),
        ],
    )
    state = RundownState()
    state.load(rundown)
    state.begin(by="system")

    guarded = dispatch_rundown_control(state, "next", by="agent")
    assert guarded.success is False
    assert guarded.reject is not None and guarded.reject.reason == "min_duration_not_met"
    assert guarded.reject.remaining_ms == 60_000

    exempt = dispatch_rundown_control(state, "next", by="human")
    assert exempt.success is True  # 导演免检：min_duration 只约束 Agent 抢跑
