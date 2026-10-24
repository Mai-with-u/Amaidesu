"""目标账本直测：三簿记账与目标↔任务账/委派账联动，不建 Agent、不连游戏。

覆盖：受理登记（内存簿 + 任务账）、暂停簿进出、可行动目标查询、终态收账
（含批级委派共享结算与状态粘滞）、查不到目标的失败收账、停滞标记、
结束/暂停通知文案。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from src.agents.minecraft.goal_ledger import GoalLedger, finish_notice, goal_label, pause_notice
from src.agents.minecraft.maicraft import goal_run_of


class _FakeLedger:
    """框架任务账替身：记录 update 调用；get 按登记表返回。"""

    def __init__(self) -> None:
        self.updates: list[Dict[str, Any]] = []
        self.records: Dict[str, Any] = {}

    def update(self, task_id: str, status: str, **kwargs: Any) -> None:
        self.updates.append({"task_id": task_id, "status": status, **kwargs})

    def get(self, task_id: str) -> Optional[Any]:
        return self.records.get(task_id)


def _tracker(ledger: Optional[_FakeLedger] = None) -> MagicMock:
    tracker = MagicMock()
    tracker.ledger = ledger
    return tracker


def _run(**overrides: Any) -> Any:
    data = {"goal_id": 1, "ability": "maicraft:gather", "state": "running"}
    data.update(overrides)
    run = goal_run_of(data)
    assert run is not None
    return run


def test_track_registers_note_and_task_ledger() -> None:
    tracker = _tracker(_FakeLedger())
    ledger = GoalLedger(task_tracker=tracker, agent_name="minecraft")
    ledger.track(_run(state="running", doing="走向树"))

    assert ledger.note_of(1)["doing"] == "走向树"
    # 任务账登记走注入的 tracker：编号带 maicraft-goal- 前缀、发起方是 Agent 注册名
    tracker.track.assert_called_once()
    kwargs = tracker.track.call_args.kwargs
    assert kwargs["task_id"] == "maicraft-goal-1" and kwargs["initiator"] == "minecraft"


def test_paused_book_and_actionable_query() -> None:
    ledger = GoalLedger()
    ledger.track(_run(goal_id=2, state="awaiting_answer", question={"text": "往哪边走？"}))
    ledger.track(_run(goal_id=3, state="paused"))
    ledger.track(_run(goal_id=4, state="running"))

    assert ledger.actionable_goal_ids() == [2, 3]
    # 恢复（状态更新为 running）后移出暂停簿，不再可行动
    ledger.note(_run(goal_id=3, state="running"))
    assert ledger.actionable_goal_ids() == [2]


def test_settle_finished_closes_books_and_settles_batch() -> None:
    book = _FakeLedger()
    book.records["deleg-9"] = MagicMock(snapshot={})
    tracker = _tracker(book)
    ledger = GoalLedger(task_tracker=tracker, agent_name="minecraft")
    ledger.track(_run(state="running"))
    ledger.note(_run(goal_id=1, state="running"))

    finished = _run(state="finished", result={"status": "done", "summary": "砍了 5 块原木"})
    ledger.settle_finished(finished, batch_task_ids=["deleg-9", "deleg-gone"])

    assert ledger.note_of(1) == {} and ledger.actionable_goal_ids() == []
    assert ledger.recent_finished()[-1]["status"] == "done"
    statuses = {u["task_id"]: u["status"] for u in book.updates}
    assert statuses["maicraft-goal-1"] == "succeeded"
    # 批内委派共享终态；已不在册的委派（LLM 交付先写过终态）状态粘滞不回退
    assert statuses["deleg-9"] == "succeeded"


def test_mark_gone_records_failure_and_returns_last_note() -> None:
    book = _FakeLedger()
    ledger = GoalLedger(task_tracker=_tracker(book), agent_name="minecraft")
    ledger.track(_run(goal_id=7, state="running", doing="挖到一半"))

    note = ledger.mark_gone(7, "换了世界")
    assert note["doing"] == "挖到一半"
    assert ledger.recent_finished()[-1]["status"] == "gone"
    assert book.updates[-1]["task_id"] == "maicraft-goal-7" and book.updates[-1]["status"] == "failed"


def test_stalled_note_stamps_task_and_batch_snapshots() -> None:
    book = _FakeLedger()
    book.records["deleg-1"] = MagicMock(snapshot={"progress": 1})
    ledger = GoalLedger(task_tracker=_tracker(book), agent_name="minecraft")
    ledger.track(_run(goal_id=5, state="running"))

    ledger.note_stalled(_run(goal_id=5, state="running", doing="挖石料"), batch_task_ids=["deleg-1"])

    goal_update = next(u for u in book.updates if u["task_id"] == "maicraft-goal-5")
    assert goal_update["snapshot"]["stalled"] is True
    batch_update = next(u for u in book.updates if u["task_id"] == "deleg-1")
    assert batch_update["snapshot"]["stalled_goals"] == {"5": "maicraft:gather"}


def test_finish_notice_mentions_folded_path_and_self_brief() -> None:
    run = _run(state="done", result={"status": "done", "summary": "完了", "folded": [{"path": "/attempts"}]})
    notice = finish_notice(run, "在河边，血量满。")
    assert goal_label(run) in notice and "结束时：在河边，血量满。" in notice
    assert "result.folded" in notice and "maicraft_goal(operation=get, goal_id=1, path=" in notice


def test_pause_notice_prefers_mod_reason_and_ignores_stale_urgent() -> None:
    run = _run(state="paused", doing="目标 maicraft:build 已暂停（第 2 步）")
    fresh = pause_notice(run, False, ("已撤离", 100.0), 110.0)
    assert "暂停前身体刚处理完一件急事：已撤离" in fresh

    stale = pause_notice(run, False, ("已撤离", 0.0), 200.0)
    assert "Mod 没写暂停原因" in stale and "已撤离" not in stale

    mod_reason = _run(state="paused", doing="目标 x 已暂停（第 0 步）：撤离后离打点 31 格，主任务停在半路")
    assert "Mod 说：" in pause_notice(mod_reason, False, None, 0.0)
