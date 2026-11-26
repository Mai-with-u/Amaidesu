"""解说前提守卫的单测：两条件、辅助能力排除、2 秒默认窗、静默提醒豁免条件①保留条件②、结构化拒回与提示词对齐。

另覆盖 GoalWatch 的下达时刻记录与辅助能力过滤（守卫条件①与条件②的共同数据源）。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.agents.minecraft.commentary_guard import (
    CONDITION_RECENTLY_ISSUED,
    CONDITION_SETTLED_DURING_STEP,
    DEFAULT_GUARD_WINDOW_MS,
    CommentaryPremiseGuard,
    is_exempt,
    is_guarded_ability,
)
from src.agents.minecraft.goals import GoalWatch
from src.agents.minecraft.lead_mode import LEAD_MODE_PROMPT_SECTION
from src.agents.minecraft.maicraft import GoalRun
from src.modules.agents.control import _COMMENTARY_DESCRIPTION
from tests.helpers import FakeClock

# 守卫判定用的假时刻表：编号 → 下达时刻（单调毫秒）


def _pending(pairs: Dict[int, int]) -> Dict[int, int]:
    return dict(pairs)


def _make_guard(clock: FakeClock, window_ms: int = DEFAULT_GUARD_WINDOW_MS) -> CommentaryPremiseGuard:
    return CommentaryPremiseGuard(window_ms=window_ms, clock_ms=clock.now_ms)


def _make_watch(clock: FakeClock) -> GoalWatch:
    async def no_call(name: str, args: Dict[str, Any]) -> Any:  # pragma: no cover - 不会被调到
        raise AssertionError("单测不应触发 MCP 调用")

    async def no_async(*args: Any) -> None:  # pragma: no cover - 不会被调到
        raise AssertionError("单测不应触发回调")

    return GoalWatch(
        call=no_call,
        connected=lambda: True,
        on_goal_changed=no_async,
        on_goal_gone=no_async,
        on_body_event=no_async,
        clock_ms=clock.now_ms,
    )


def _run(goal_id: int, *, ability: str = "mine", finished: bool = False) -> GoalRun:
    return GoalRun(
        goal_id=goal_id,
        ability=ability,
        purpose="测试目标",
        state="running" if not finished else "finished",
        question=None,
        result=None,
        doing="",
        data={},
    )


# ----- 默认窗与条件①：刚下达未落稳 -----


def test_default_guard_window_is_two_seconds() -> None:
    clock = FakeClock()
    guard = CommentaryPremiseGuard(clock_ms=clock.now_ms)
    assert DEFAULT_GUARD_WINDOW_MS == 2_000
    assert guard.window_ms == 2_000


def test_condition1_recently_issued_goal_rejects_with_structured_payload() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(5_000)
    guard.begin_step(set())
    # 下达 1 秒后（默认 2 秒窗内）提交解说：拒绝，返回结构化条件与客观提示文案
    rejection = guard.evaluate(_pending({12: clock.now_ms() - 1_000}))
    assert rejection is not None
    assert rejection.condition == CONDITION_RECENTLY_ISSUED
    assert rejection.blocking_goal_ids == (12,)
    assert rejection.window_ms == 2_000
    assert "#12" in rejection.message
    assert "2 秒" in rejection.message
    assert "发起目标前提交解说" in rejection.message
    assert "有了结果再提交解说" not in rejection.message


def test_condition1_window_elapsed_passes() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(5_000)
    guard.begin_step(set())
    # 下达已满 2 秒瞬态窗：放行（保住正常推进期实况解说）
    assert guard.check(_pending({12: clock.now_ms() - 2_000})) is None


def test_condition1_lists_all_unsettled_goals() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(5_000)
    guard.begin_step(set())
    rejection = guard.evaluate(_pending({5: clock.now_ms() - 500, 3: clock.now_ms() - 1_000}))
    assert rejection is not None
    assert rejection.blocking_goal_ids == (3, 5)
    assert "#3" in rejection.message and "#5" in rejection.message


# ----- 条件②：步开始快照里的目标本步变为终态 -----


def test_condition2_goal_settled_during_step_rejects() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    # 步开始时 #7 在受守卫未决名单
    guard.begin_step({7})
    clock.tick(2_000)
    # 推理期间 #7 变为终态（不在当前未决表里）：解说依据在写作期间变了，拒绝
    rejection = guard.evaluate(_pending({}))
    assert rejection is not None
    assert rejection.condition == CONDITION_SETTLED_DURING_STEP
    assert rejection.blocking_goal_ids == (7,)
    assert rejection.window_ms == 2_000
    assert "#7" in rejection.message


def test_condition2_still_pending_goals_do_not_trigger() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step({7})
    clock.tick(3_000)
    # 快照里的目标已过 2 秒暂扣窗但仍在跑：不属条件②，放行
    assert guard.check(_pending({7: 1_000})) is None


def test_snapshot_refreshes_each_step() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step({7})
    clock.tick(2_000)
    # 上一步结束时 #7 变终态被拒；下一步重新拍快照后，旧终态不再触发拒绝
    first = guard.check(_pending({}))
    assert first is not None
    guard.begin_step(set())
    assert guard.check(_pending({})) is None


# ----- 静默提醒豁免条件①但保留条件② -----


def test_skip_condition1_bypasses_window_but_keeps_condition2() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(5_000)
    guard.begin_step({7})
    # 收到静默提醒后的首次解说：#12 刚下达 500ms 且 #7 仍在跑 -> 跳过条件①放行
    assert (
        guard.evaluate(
            _pending({7: 1_000, 12: clock.now_ms() - 500}),
            skip_condition1=True,
        )
        is None
    )
    # 但若本步推理期间 #7 变为终态，即使 skip_condition1=True 也必须由条件②拦截
    rejection = guard.evaluate(
        _pending({12: clock.now_ms() - 500}),
        skip_condition1=True,
    )
    assert rejection is not None
    assert rejection.condition == CONDITION_SETTLED_DURING_STEP
    assert rejection.blocking_goal_ids == (7,)


# ----- 豁免与非世界状态变更辅助能力排除 -----


def test_exempt_arguments() -> None:
    assert is_exempt({"correction": True}) is True
    assert is_exempt({"correction": "true"}) is True
    assert is_exempt({"urgency": "high"}) is True
    assert is_exempt({"urgency": "HIGH"}) is True


def test_normal_commentary_not_exempt() -> None:
    assert is_exempt({}) is False
    assert is_exempt({"urgency": "normal"}) is False
    assert is_exempt({"correction": False}) is False


@pytest.mark.parametrize(
    ("ability", "expected"),
    [
        ("maicraft:chat", False),
        ("chat", False),
        ("MAICRAFT:CHAT", False),
        ("maicraft:find", False),
        ("find", False),
        ("maicraft:body_language", False),
        ("body_language", False),
        ("maicraft:timer", False),
        ("timer", False),
        ("maicraft:mine", True),
        ("gather", True),
        ("maicraft:build", True),
        ("maicraft:goto", True),
        ("", True),
    ],
)
def test_is_guarded_ability_excludes_non_world_auxiliary_abilities(ability: str, expected: bool) -> None:
    assert is_guarded_ability(ability) is expected


# ----- GoalWatch 下达时刻（条件①与条件②共同数据源） -----


def test_goal_watch_records_issuance_time_and_excludes_auxiliary_abilities() -> None:
    clock = FakeClock()
    watch = _make_watch(clock)
    clock.tick(5_000)
    watch.track(_run(9, ability="maicraft:gather"))
    watch.track(_run(10, ability="maicraft:chat"))
    watch.track(_run(11, ability="find"))
    watch.track(_run(12, ability="maicraft:body_language"))
    watch.track(_run(13, ability="timer"))
    # tracked_ids 仍保留全部跟踪目标（急停/取消/事件同步照旧）
    assert watch.tracked_ids() == [9, 10, 11, 12, 13]
    # pending_issuance 仅保留受守卫的世界操作目标，排除 4 种辅助能力
    assert watch.pending_issuance() == {9: clock.now_ms()}

    # 条件②快照用 pending_issuance().keys() 时，辅助能力在推理期间结束也不会触发条件②误杀
    guard = _make_guard(clock)
    guard.begin_step(watch.pending_issuance().keys())
    clock.tick(2_500)
    for aux_id in (10, 11, 12, 13):
        watch.untrack(aux_id)
    assert guard.evaluate(watch.pending_issuance()) is None


def test_goal_watch_skips_finished_and_untracks() -> None:
    clock = FakeClock()
    watch = _make_watch(clock)
    clock.tick(5_000)
    watch.track(_run(9, finished=True))
    assert watch.pending_issuance() == {}
    watch.track(_run(10))
    watch.untrack(10)
    assert watch.pending_issuance() == {}


def test_prompt_and_tool_description_align_with_plan_before_execute() -> None:
    assert "先调用 `framework_commentary` 交代接下来的打算" in LEAD_MODE_PROMPT_SECTION
    assert "把解说放在执行调用之前" in LEAD_MODE_PROMPT_SECTION
    assert "先提交解说交代打算" in _COMMENTARY_DESCRIPTION
    assert "不提前夹带未确认的完成结论" in _COMMENTARY_DESCRIPTION
