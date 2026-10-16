"""RundownState 状态机：假时钟驱动全部时间场景，emit / on_changed 用捕获列表验证唯一变更边界的发射与通知契约"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pytest

from src.agents.streamer.rundown.rundown_state import RundownReject, RundownState
from src.modules.storage.models.rundown import Rundown, RundownInstance, RundownInstanceSegment, RundownSegment


class _FakeClock:
    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _segment(seg_id: str, title: str, expected_ms: int = 600_000, **kwargs: Any) -> RundownSegment:
    return RundownSegment(id=seg_id, title=title, task_description=f"{title}目标", expected_ms=expected_ms, **kwargs)


def _rundown(*segments: RundownSegment, title: str = "测试流程单", rundown_id: str = "test_rd") -> Rundown:
    return Rundown(rundown_id=rundown_id, title=title, segments=list(segments))


def _default_rundown() -> Rundown:
    """三段：opening 带 min_duration 守卫，chat / closing 无。"""
    return _rundown(
        _segment("opening", "开场", min_duration_ms=60_000),
        _segment("chat", "闲聊"),
        _segment("closing", "收尾", 300_000),
    )


class _Harness:
    def __init__(self) -> None:
        self.clock = _FakeClock()
        self.events: List[Tuple[str, Any]] = []
        self.changed: List[Tuple[str, str]] = []
        self.state = RundownState(
            emit=lambda name, payload: self.events.append((name, payload)),
            on_changed=lambda segment_id, by: self.changed.append((segment_id, by)),
            clock=self.clock,
        )

    @property
    def now(self) -> int:
        return self.clock.now

    def load(self, rundown: Rundown | None = None) -> "_Harness":
        self.state.load(rundown or _default_rundown(), now_ms=self.now)
        return self

    def drive_to_last(self) -> None:
        """推进到末段（human 免 min_duration 检查）。"""
        self.clock.advance(60_001)
        assert self.state.next(by="human") is None and self.state.next(by="human") is None
        assert self.state.current_segment_id == "closing"

    def finish(self) -> None:
        self.drive_to_last()
        assert self.state.next(by="human") is None and self.state.status == "done"


@pytest.fixture
def h() -> _Harness:
    return _Harness().load()


def test_initial_state_and_load_emit() -> None:
    idle = RundownState()
    assert (idle.status, idle.get_snapshot()["rundown_id"], idle.get_snapshot()["status"]) == ("idle", "", "idle")

    harness = _Harness().load()
    state = harness.state
    assert (state.status, state.current_segment_id, state.rundown_started_at_ms) == ("running", "opening", harness.now)
    ((name, payload),) = harness.events
    assert (name, payload.segment_id, payload.index, payload.total, payload.by) == (
        "rundown.changed",
        "opening",
        0,
        3,
        "system",
    )
    assert harness.changed == [("opening", "system")]

    # 重新 load 清掉此前的暂停累计
    state.pause(by="human", now_ms=harness.now)
    harness.load()
    assert state.paused_at_ms is None and state.get_current_remaining_ms(now_ms=harness.now) == 600_000


def test_goto_moves_and_notifies(h: _Harness) -> None:
    h.events.clear()
    h.changed.clear()
    h.clock.advance(60_001)
    assert h.state.goto("chat", by="agent", now_ms=h.now) is None
    assert (h.state.index, h.state.current_segment_id) == (1, "chat")
    assert (h.events[-1][1].segment_id, h.events[-1][1].by) == ("chat", "agent")
    assert h.changed == [("chat", "agent")]


def test_goto_and_next_reject_agent_inside_min_duration_but_not_human(h: _Harness) -> None:
    reject = h.state.goto("chat", by="agent", now_ms=h.now)
    assert isinstance(reject, RundownReject) and reject.reason == "min_duration_not_met"
    assert reject.remaining_ms == 60_000 and h.state.index == 0
    assert h.state.next(by="agent", now_ms=h.now).reason == "min_duration_not_met"

    assert h.state.goto("chat", by="human", now_ms=h.now) is None and h.state.index == 1
    h.load()
    assert h.state.next(by="human", now_ms=h.now) is None and h.state.current_segment_id == "chat"
    h.load()
    h.clock.advance(60_000)
    assert h.state.next(by="agent", now_ms=h.now) is None and h.state.current_segment_id == "chat"


def test_goto_unknown_id_and_no_rundown_are_rejected_without_mutation(h: _Harness) -> None:
    h.events.clear()
    reject = h.state.goto("nope", by="agent", now_ms=h.now)
    assert reject.reason == "unknown_segment_id" and reject.available_ids == ["opening", "chat", "closing"]
    assert h.state.index == 0 and h.events == []
    assert RundownState().goto("opening", by="human").reason == "no_rundown_loaded"


def test_next_finishes_then_rejects_further_advance(h: _Harness) -> None:
    h.drive_to_last()
    h.events.clear()
    assert h.state.next(by="agent", now_ms=h.now) is None
    assert (h.state.status, h.state.index, h.state.current_segment_id) == ("done", 3, "")
    finish = h.events[-1][1]
    assert (finish.segment_id, finish.index, finish.total) == ("", 3, 3) and h.changed[-1] == ("", "agent")
    assert h.state.next(by="human", now_ms=h.now).reason == "already_done"


@pytest.mark.parametrize("method", ["goto", "pause", "resume", "renew", "next"])
def test_invalid_actor_raises(h: _Harness, method: str) -> None:
    args = ("chat",) if method == "goto" else ()
    with pytest.raises(ValueError):
        getattr(h.state, method)(*args, by="robot", now_ms=h.now)


def test_pause_freezes_elapsed_and_resume_accumulates(h: _Harness) -> None:
    h.clock.advance(100_000)
    assert h.state.pause(by="human", now_ms=h.now) is None and h.state.status == "paused"
    h.clock.advance(200_000)
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 500_000
    assert h.state.resume(by="human", now_ms=h.now) is None and h.state.status == "running"
    h.clock.advance(50_000)
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 450_000


def test_pause_resume_rejections(h: _Harness) -> None:
    assert RundownState().pause(by="human").reason == "not_running"
    assert h.state.resume(by="human", now_ms=h.now).reason == "not_paused"
    assert h.state.pause(by="human", now_ms=h.now) is None
    assert h.state.pause(by="human", now_ms=h.now).reason == "not_running"


def test_session_progress_freezes_during_pause_and_excludes_pause_after_resume(h: _Harness) -> None:
    """整场进度/已播时长扣除暂停：暂停中冻结，恢复后不把暂停时长计入已播。"""
    h.clock.advance(100_000)
    assert h.state.pause(by="human", now_ms=h.now) is None
    h.clock.advance(200_000)
    # 暂停中整场口径冻结在暂停起点
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 100_000
    progress_paused = h.state.get_progress_percent(now_ms=h.now)
    assert progress_paused is not None

    h.clock.advance(300_000)
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 100_000  # 仍冻结
    assert h.state.get_progress_percent(now_ms=h.now) == progress_paused

    assert h.state.resume(by="human", now_ms=h.now) is None
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 100_000  # 暂停的 500 秒不进已播
    h.clock.advance(50_000)
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 150_000


def test_resume_accumulates_both_segment_and_session_pause(h: _Harness) -> None:
    h.clock.advance(100_000)
    h.state.pause(by="human", now_ms=h.now)
    h.clock.advance(200_000)
    assert h.state.resume(by="human", now_ms=h.now) is None
    assert (h.state._accumulated_pause_ms, h.state._accumulated_session_pause_ms) == (200_000, 200_000)

    # 二次暂停叠加累计
    h.clock.advance(50_000)
    h.state.pause(by="human", now_ms=h.now)
    h.clock.advance(30_000)
    assert h.state.resume(by="human", now_ms=h.now) is None
    assert (h.state._accumulated_pause_ms, h.state._accumulated_session_pause_ms) == (230_000, 230_000)


@pytest.mark.parametrize("action", ["goto", "next", "finish"])
def test_pause_in_segment_switch_settles_into_session_progress(h: _Harness, action: str) -> None:
    """暂停态下 goto / next / finish 切换环节，暂停时长结算进整场累计而不蒸发。"""
    if action == "finish":
        h.drive_to_last()
    h.clock.advance(100_000)
    assert h.state.pause(by="human", now_ms=h.now) is None
    baseline = h.state.get_elapsed_live_ms(now_ms=h.now)  # 暂停起点前的真实已播
    h.clock.advance(200_000)

    if action == "goto":
        assert h.state.goto("chat", by="human", now_ms=h.now) is None
    elif action == "next":
        assert h.state.next(by="human", now_ms=h.now) is None
    else:
        assert h.state.next(by="human", now_ms=h.now) is None  # finish

    if action != "finish":
        # 新环节从切换时刻起算，暂停的 200 秒不进环节计时
        assert h.state.get_current_remaining_ms(now_ms=h.now) == 600_000
    # 整场口径已扣除这 200 秒暂停：已播冻结在暂停起点
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == baseline
    h.clock.advance(50_000)
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == baseline + 50_000


def test_replace_definition_deleting_current_segment_in_pause_keeps_session_pause(h: _Harness) -> None:
    h.drive_to_last()
    h.clock.advance(100_000)
    assert h.state.pause(by="human", now_ms=h.now) is None
    h.clock.advance(200_000)
    assert _replace(h, _segment("opening", "开场"), _segment("chat", "闲聊")) is None
    # 当前环节被删回到未开始，但暂停时长已结算进整场累计
    # （drive_to_last 走了 60_001ms，环节内又播了 100_000ms；200_000ms 暂停不计）
    assert h.state.paused_at_ms is None
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 160_001
    h.clock.advance(50_000)
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 210_001


def _replace(h: _Harness, *segments: RundownSegment, rundown_id: str = "test_rd"):
    return h.state.replace_definition(_rundown(*segments, title="v2", rundown_id=rundown_id), now_ms=h.now)


def test_replace_definition_keeps_position_and_elapsed(h: _Harness) -> None:
    h.clock.advance(120_000)
    assert h.state.next(by="human") is None
    h.clock.advance(30_000)  # chat 段已播 30s

    updated = (
        _segment("opening", "开场"),
        _segment("chat", "闲聊", 900_000),
        _segment("qa", "问答", 300_000),
        _segment("closing", "收尾", 300_000),
    )
    assert _replace(h, *updated) is None

    assert (h.state.current_segment_id, h.state.status) == ("chat", "running")
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 900_000 - 30_000  # 计时锚点保留，剩余按新时长算
    name, payload = h.events[-1]
    assert (name, payload.segment_id, payload.total, payload.by) == ("rundown.changed", "chat", 4, "human")
    assert h.changed[-1] == ("chat", "human") and h.state.get_transitions()[-1]["action"] == "reload"


def test_replace_definition_edge_cases(h: _Harness) -> None:
    # 不同 id 拒绝
    reject = _replace(h, _segment("only", "唯一环节", 60_000), rundown_id="other_rd")
    assert reject.reason == "rundown_id_mismatch" and h.state.rundown.rundown_id == "test_rd"

    # 暂停冻结保持
    h.clock.advance(100_000)
    h.state.pause(by="human", now_ms=h.now)
    assert _replace(h, _segment("opening", "开场", 800_000), _segment("chat", "闲聊")) is None
    h.clock.advance(200_000)
    assert h.state.status == "paused" and h.state.get_current_remaining_ms(now_ms=h.now) == 800_000 - 100_000

    # 当前环节被删：游标重置为未开始，由 Agent 从情境重新定位
    h.load()
    h.drive_to_last()
    h.events.clear()
    assert _replace(h, _segment("opening", "开场"), _segment("chat", "闲聊")) is None
    assert (h.state.index, h.state.current_segment, h.state.status) == (-1, None, "running")
    assert h.events[-1][1].segment_id == ""

    # 已完成状态保持
    h.load()
    h.finish()
    assert _replace(h, _segment("opening", "开场"), _segment("chat", "闲聊")) is None
    assert h.state.status == "done"


def test_derived_views(h: _Harness) -> None:
    assert RundownState().get_progress_percent() is None
    assert RundownState().get_current_remaining_ms() == 0
    assert (RundownState().build_context_text(), RundownState().get_elapsed_live_ms()) == (None, None)

    early = h.state.get_progress_percent(now_ms=h.now)
    assert early is not None and early >= 0.0
    h.clock.advance(60_000)
    snapshot = h.state.get_snapshot(now_ms=h.now)
    assert (snapshot["status"], snapshot["rundown_id"], snapshot["title"]) == ("running", "test_rd", "测试流程单")
    assert (snapshot["index"], snapshot["total"], snapshot["paused"]) == (0, 3, False)
    assert (snapshot["current"]["id"], snapshot["current"]["elapsed_ms"], snapshot["current"]["remaining_ms"]) == (
        "opening",
        60_000,
        540_000,
    )
    assert h.state.get_elapsed_live_ms() == 60_000

    h.clock.advance(10_000_000_000)
    assert h.state.get_progress_percent(now_ms=h.now) == 100.0


def test_transitions_are_capped_and_emit_failures_are_soft() -> None:
    clock = _FakeClock()
    state = RundownState(clock=clock)  # 无 emit / on_changed 也能工作
    state.load(_default_rundown(), now_ms=clock.now)
    for _ in range(60):
        assert state.goto("chat", by="human", now_ms=clock.now) is None
    transitions = state.get_transitions()
    assert (len(transitions), transitions[-1]["action"]) == (50, "goto")

    def boom(name: str, payload: Any) -> None:
        raise RuntimeError("bus down")

    noisy = RundownState(emit=boom, clock=clock)
    noisy.load(_default_rundown(), now_ms=clock.now)
    assert noisy.goto("chat", by="human", now_ms=clock.now) is None and noisy.index == 1


def test_build_context_text_renders_segment_fields_and_done_facts() -> None:
    harness = _Harness().load(
        _rundown(
            RundownSegment(
                id="opening",
                title="开场",
                task_description="开场目标" + chr(10) + "要点：问好、预告",
                expected_ms=600_000,
                notes="备注内容",
            ),
            _segment("chat", "闲聊"),
            rundown_id="ctx_rd",
        )
    )
    harness.clock.advance(60_000)
    text = harness.state.build_context_text(now_ms=harness.now)
    assert text is not None
    for fragment in (
        "[流程单] 环节 1/2：开场",
        "剩 约 9 分钟",
        "目标：开场目标",
        "要点：问好、预告",
        "备注：备注内容",
        # 要点已并入 task_description（key_points 字段删除），随"目标"行一并渲染
        "后续环节：闲聊(chat)",
        "rundown_control",
    ):
        assert fragment in text

    # done 态不断供：返回完成事实块而非 None，防"（当前无流程单）"被误读为该重新开场
    done = _Harness().load()
    done.finish()
    done_text = done.state.build_context_text(now_ms=done.now)
    assert done_text is not None
    assert "已全部完成（3/3 环节）" in done_text
    assert "自由互动" in done_text
    assert "重启或切换" in done_text


def test_renew_resets_timer_keeps_segment_and_emits(h: _Harness) -> None:
    h.clock.advance(700_000)
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 0
    assert h.state.renew(by="agent", now_ms=h.now) is None

    assert (h.state.current_segment_id, h.state.index) == ("opening", 0)
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 600_000
    assert h.state.is_current_segment_overdue(now_ms=h.now) is False
    assert h.events[-1][0] == "rundown.changed" and h.state.get_transitions()[-1]["action"] == "renew"

    # 续期清空累计暂停，旧暂停折算不带入新周期
    h.clock.advance(60_000)
    h.state.pause(by="human", now_ms=h.now)
    h.clock.advance(30_000)
    h.state.resume(by="human", now_ms=h.now)
    h.state.renew(by="human", now_ms=h.now)
    h.clock.advance(600_000)
    assert (
        h.state.get_current_remaining_ms(now_ms=h.now) == 0
        and h.state.is_current_segment_overdue(now_ms=h.now) is False
    )


def test_renew_rejections(h: _Harness) -> None:
    assert RundownState().renew(by="agent").reason == "no_rundown_loaded"
    h.state.pause(by="human", now_ms=h.now)
    assert h.state.renew(by="agent", now_ms=h.now).reason == "not_running" and h.state.paused_at_ms is not None
    h.state.resume(by="human", now_ms=h.now)
    h.finish()
    assert h.state.renew(by="agent", now_ms=h.now).reason == "already_done"


def test_overdue_context_text_names_the_way_out_then_recovers_after_renew(h: _Harness) -> None:
    """超时后明确标注已超时与出路，不渲染"剩 0 秒"僵尸态；续期后恢复正常展示。"""
    h.clock.advance(760_000)
    text = h.state.build_context_text(now_ms=h.now)
    assert "已超时 约 3 分钟" in text and "剩 0 秒" not in text and "renew" in text and "goto/next" in text

    h.state.renew(by="agent", now_ms=h.now)
    h.clock.advance(60_000)
    recovered = h.state.build_context_text(now_ms=h.now)
    assert "剩 约 9 分钟" in recovered and "已超时" not in recovered


def test_last_segment_context_adds_renew_guidance_until_overdue(h: _Harness) -> None:
    """末段（后续环节为空、未超时）追加续期引导；超时分支已有 renew 提示，不重复追加。"""
    h.drive_to_last()
    text = h.state.build_context_text(now_ms=h.now)
    assert "最后一环" in text and "renew 延长本环节" in text

    h.clock.advance(600_000)
    overdue_text = h.state.build_context_text(now_ms=h.now)
    assert "已超时" in overdue_text and overdue_text.count("renew") == 1


def test_restart_rejects_idle_and_agent() -> None:
    """未加载结构化拒绝；agent 不在 restart 白名单（单的生死归导演）。"""
    assert RundownState().restart(by="human").reason == "no_rundown_loaded"
    h = _Harness().load()
    with pytest.raises(ValueError):
        h.state.restart(by="agent")


def test_restart_resets_running_to_first_segment(h: _Harness) -> None:
    h.drive_to_last()
    h.clock.advance(120_000)
    assert h.state.restart(by="human") is None
    assert (h.state.status, h.state.index, h.state.current_segment_id) == ("running", 0, "opening")
    # 环节与整场锚点都重置为 now：计时归零
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 600_000
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 0
    assert h.events[-1][0] == "rundown.changed" and h.state.get_transitions()[-1]["action"] == "restart"


def test_restart_clears_paused(h: _Harness) -> None:
    h.state.pause(by="human", now_ms=h.now)
    h.clock.advance(30_000)
    assert h.state.restart(by="human") is None
    assert (h.state.status, h.state.paused_at_ms) == ("running", None)
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 600_000
    # 旧暂停累计不带入重开后的整场计时：推进量即已播量（漏清整场累计会虚慢）
    h.clock.advance(10_000)
    assert h.state.get_elapsed_live_ms(now_ms=h.now) == 10_000


def test_restart_from_done_reopens(h: _Harness) -> None:
    h.finish()
    h.clock.advance(60_000)
    assert h.state.restart(by="human") is None
    assert (h.state.status, h.state.index, h.state.current_segment_id) == ("running", 0, "opening")


# ---------------------------------------------------------------------------
# append（Agent 计划扩展权）
# ---------------------------------------------------------------------------


def _append_args() -> RundownSegment:
    return _segment("bonus", "临时加场", 120_000)


def test_append_in_running_keeps_cursor_and_lists_upcoming(h: _Harness) -> None:
    h.clock.advance(60_000)
    assert h.state.append_segment(_append_args(), by="agent", now_ms=h.now) is None
    # running 态游标不动：新环节只是进 upcoming 列表，Agent 可 goto 过去
    assert (h.state.status, h.state.index, h.state.current_segment_id) == ("running", 0, "opening")
    # 计时锚点不被 append 打断：elapsed 连续
    assert h.state._segment_elapsed_ms(h.now) == 60_000
    assert [s.id for s in h.state.rundown.segments][-1] == "bonus"


def test_append_in_paused_stays_frozen(h: _Harness) -> None:
    h.state.pause(by="human", now_ms=h.now)
    frozen = h.state.paused_at_ms
    assert h.state.append_segment(_append_args(), by="agent", now_ms=h.now) is None
    # paused 态保持暂停冻结：锚点与暂停态原样
    assert (h.state.status, h.state.paused_at_ms, h.state.index) == ("paused", frozen, 0)


def test_append_after_done_extends_lifecycle(h: _Harness) -> None:
    h.finish()
    h.clock.advance(60_000)
    assert h.state.append_segment(_append_args(), by="agent", now_ms=h.now) is None
    # 续命：游标指到新追加环节（原 total 位置），锚点重置、status 派生回 running
    assert (h.state.status, h.state.index, h.state.current_segment_id) == ("running", 3, "bonus")
    assert h.state.get_current_remaining_ms(now_ms=h.now) == 120_000
    assert h.events[-1][0] == "rundown.changed"
    payload = h.events[-1][1]
    assert (payload.action, payload.segment_id, payload.index, payload.total) == ("append", "bonus", 3, 4)


def test_append_duplicate_rejected_with_available_ids(h: _Harness) -> None:
    reject = h.state.append_segment(_segment("opening", "重复 id"), by="agent", now_ms=h.now)
    assert reject is not None and reject.reason == "duplicate_segment_id"
    assert reject.available_ids == ["opening", "chat", "closing"]
    assert len(h.state.rundown.segments) == 3  # 拒绝不变更


def test_append_limit_reached(h: _Harness) -> None:
    many = _rundown(*(_segment(f"s{i}", f"环节{i}") for i in range(20)))
    h.state.load(many, now_ms=h.now)
    reject = h.state.append_segment(_append_args(), by="agent", now_ms=h.now)
    assert reject is not None and reject.reason == "limit_reached"
    assert len(h.state.rundown.segments) == 20


def test_append_idle_rejected_and_invalid_by_raises() -> None:
    state = RundownState()
    assert state.append_segment(_append_args(), by="agent") is not None
    assert state.append_segment(_append_args(), by="agent").reason == "no_rundown_loaded"
    with pytest.raises(ValueError):
        state.append_segment(_append_args(), by="human")


# ---------------------------------------------------------------------------
# restore（崩溃恢复）与 export_progress（实例写穿）
# ---------------------------------------------------------------------------


def _snapshot(h: _Harness, *, seg_index: int, started_at: int, paused_at: Optional[int] = None) -> RundownInstance:
    rundown = h.state.rundown
    assert rundown is not None
    return RundownInstance(
        live_session_id=7,
        template_id=rundown.rundown_id,
        seg_index=seg_index,
        segment_started_at_ms=started_at,
        paused_at_ms=paused_at,
        rundown_started_at_ms=started_at,
        accumulated_pause_ms=0,
        accumulated_session_pause_ms=0,
        updated_at_ms=h.now,
        segments=[
            RundownInstanceSegment(
                seq=i, seg_id=s.id, title=s.title, task_description=s.task_description, expected_ms=s.expected_ms
            )
            for i, s in enumerate(rundown.segments)
        ],
    )


def test_restore_running_snapshot_elapsed_includes_downtime(h: _Harness) -> None:
    h.clock.advance(60_000)
    started_at = 1_000_000
    snapshot = _snapshot(h, seg_index=1, started_at=started_at)
    # 全新状态机模拟崩溃后重启：时钟已走过"宕机时间"
    fresh = _Harness()
    fresh.clock.now = h.now + 30_000
    assert fresh.state.restore(_default_rundown(), snapshot, now_ms=fresh.now) is None
    # 绝对时刻语义：恢复后 elapsed 自然包含宕机时间（60s 停留 + 30s 宕机）
    assert (fresh.state.status, fresh.state.index, fresh.state.current_segment_id) == ("running", 1, "chat")
    assert fresh.state._segment_elapsed_ms(fresh.now) == 90_000
    assert fresh.state.get_current_remaining_ms(now_ms=fresh.now) == 600_000 - 90_000
    assert fresh.events[-1][0] == "rundown.changed"
    payload = fresh.events[-1][1]
    assert (payload.action, payload.by, payload.segment_id) == ("restore", "system", "chat")


def test_restore_paused_snapshot_keeps_freeze(h: _Harness) -> None:
    h.clock.advance(60_000)
    paused_at = h.now
    snapshot = _snapshot(h, seg_index=0, started_at=1_000_000, paused_at=paused_at)
    fresh = _Harness()
    fresh.clock.now = paused_at + 120_000
    assert fresh.state.restore(_default_rundown(), snapshot, now_ms=fresh.now) is None
    # 冻结语义保持：暂停中 elapsed 停在暂停时刻，宕机时间不进环节计时
    assert (fresh.state.status, fresh.state.paused_at_ms) == ("paused", paused_at)
    assert fresh.state._segment_elapsed_ms(fresh.now) == 60_000


def test_restore_corrupted_snapshot_rejected_without_mutation(h: _Harness) -> None:
    fresh = _Harness()
    empty = _snapshot(h, seg_index=0, started_at=1_000_000)
    empty.segments = []
    assert fresh.state.restore(_default_rundown(), empty, now_ms=fresh.now) is not None
    out_of_range = _snapshot(h, seg_index=99, started_at=1_000_000)
    assert fresh.state.restore(_default_rundown(), out_of_range, now_ms=fresh.now) is not None
    assert fresh.state.restore(_default_rundown(), out_of_range).reason == "invalid_snapshot"
    # 拒绝完全不动现状：仍是 idle
    assert (fresh.state.status, fresh.state.rundown) == ("idle", None)
    assert fresh.events == []


def test_restore_done_snapshot_keeps_finished_fact(h: _Harness) -> None:
    h.finish()
    snapshot = _snapshot(h, seg_index=3, started_at=1_000_000)
    fresh = _Harness()
    assert fresh.state.restore(_default_rundown(), snapshot, now_ms=fresh.now) is None
    assert (fresh.state.status, fresh.state.index) == ("done", 3)


def test_export_progress_minimal_exposure(h: _Harness) -> None:
    h.clock.advance(60_000)
    progress = h.state.export_progress()
    assert progress["template_id"] == "test_rd"
    assert progress["seg_index"] == 0
    assert progress["segment_started_at_ms"] == 1_000_000
    assert progress["paused_at_ms"] is None
    assert progress["rundown_started_at_ms"] == 1_000_000
    assert set(progress) == {
        "template_id",
        "seg_index",
        "segment_started_at_ms",
        "paused_at_ms",
        "rundown_started_at_ms",
        "accumulated_pause_ms",
        "accumulated_session_pause_ms",
    }
    idle_progress = RundownState().export_progress()
    assert idle_progress == {}
