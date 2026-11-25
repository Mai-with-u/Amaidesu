"""解说前提守卫的单测：两条件（刚下达未终态 / 步开始后变终态）、豁免、暂扣窗到期放行、快照更新。

另覆盖 GoalWatch 的下达时刻记录（守卫条件①的数据来源）。
"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.minecraft.commentary_guard import CommentaryPremiseGuard, is_exempt
from src.agents.minecraft.goals import GoalWatch
from src.agents.minecraft.maicraft import GoalRun
from tests.helpers import FakeClock

# 守卫判定用的假时刻表：编号 → 下达时刻（单调毫秒）


def _pending(pairs: Dict[int, int]) -> Dict[int, int]:
    return dict(pairs)


def _make_guard(clock: FakeClock, window_ms: int = 8000) -> CommentaryPremiseGuard:
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


def _run(goal_id: int, finished: bool = False) -> GoalRun:
    return GoalRun(
        goal_id=goal_id,
        ability="mine",
        purpose="挖石头",
        state="running" if not finished else "finished",
        question=None,
        result=None,
        doing="",
        data={},
    )


# ----- 条件①：刚下达未终态 -----


def test_condition1_recently_issued_goal_rejects() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step(set())
    # 下达 3 秒后（窗口 8 秒内）提交解说：拒绝，文案点名目标编号
    refusal = guard.check(_pending({12: clock.now_ms() - 3_000}))
    assert refusal is not None
    assert "#12" in refusal


def test_condition1_window_elapsed_passes() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step(set())
    # 下达已过 8 秒暂扣窗：放行（长任务期间的实况不受拦）
    assert guard.check(_pending({12: clock.now_ms() - 8_000})) is None


def test_condition1_lists_all_unsettled_goals() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step(set())
    refusal = guard.check(_pending({3: clock.now_ms() - 1_000, 5: clock.now_ms() - 2_000}))
    assert refusal is not None
    assert "#3" in refusal and "#5" in refusal


# ----- 条件②：步开始快照里的目标本步变为终态 -----


def test_condition2_goal_settled_during_step_rejects() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    # 步开始时 #7 在未决名单
    guard.begin_step({7})
    clock.tick(2_000)
    # 推理期间 #7 变为终态（不在当前未决表里）：解说依据在写作期间变了，拒绝
    refusal = guard.check(_pending({}))
    assert refusal is not None
    assert "#7" in refusal


def test_condition2_still_pending_goals_do_not_trigger() -> None:
    clock = FakeClock()
    guard = _make_guard(clock)
    clock.tick(1_000)
    guard.begin_step({7})
    clock.tick(9_000)
    # 快照里的目标已过暂扣窗但仍在跑：不属条件②，放行
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


# ----- 豁免 -----


def test_exempt_arguments() -> None:
    assert is_exempt({"correction": True}) is True
    assert is_exempt({"correction": "true"}) is True
    assert is_exempt({"urgency": "high"}) is True
    assert is_exempt({"urgency": "HIGH"}) is True


def test_normal_commentary_not_exempt() -> None:
    assert is_exempt({}) is False
    assert is_exempt({"urgency": "normal"}) is False
    assert is_exempt({"correction": False}) is False


# ----- GoalWatch 下达时刻（条件①数据来源） -----


def test_goal_watch_records_issuance_time() -> None:
    clock = FakeClock()
    watch = _make_watch(clock)
    clock.tick(5_000)
    watch.track(_run(9))
    assert watch.pending_issuance() == {9: clock.now_ms()}


def test_goal_watch_skips_finished_and_untracks() -> None:
    clock = FakeClock()
    watch = _make_watch(clock)
    clock.tick(5_000)
    watch.track(_run(9, finished=True))
    assert watch.pending_issuance() == {}
    watch.track(_run(10))
    watch.untrack(10)
    assert watch.pending_issuance() == {}
