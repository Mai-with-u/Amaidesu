"""委派账直测：批内追踪清单与账面写入的生命周期，不建 Agent、不连游戏。

覆盖：受理入批 → mark_running 归拢、批终态清空、挂起保留、撤单出账、
未受理拒收直写、下播强制结算（按执行者过滤 + 清单清空）、无任务基建
降级（清单保持为空、写入口全部 no-op）、finished_ids 只读视图。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.agents.minecraft.delegation_book import DelegationBook
from src.modules.tools.tasks import TaskLedger


class _FakeRecord:
    def __init__(self, source: str, executor: str) -> None:
        self.source = source
        self.executor = executor


class _FakeLedger:
    """框架任务账替身：记录 update 调用；active_task_ids/get 按登记表返回。"""

    def __init__(self) -> None:
        self.updates: List[Dict[str, Any]] = []
        self.active: Dict[str, _FakeRecord] = {}

    def update(self, task_id: str, status: str, **kwargs: Any) -> bool:
        self.updates.append({"task_id": task_id, "status": status, **kwargs})
        return True

    def active_task_ids(self) -> List[str]:
        return list(self.active)

    def get(self, task_id: str) -> Optional[_FakeRecord]:
        return self.active.get(task_id)


class _FakeTracker:
    def __init__(self, ledger: _FakeLedger) -> None:
        self.ledger = ledger


def _book(ledger: Optional[_FakeLedger] = None) -> DelegationBook:
    tracker = _FakeTracker(ledger) if ledger is not None else None
    return DelegationBook(task_tracker=tracker, agent_name="minecraft")


def test_reply_is_noted_on_running_delegations_without_counting_as_progress() -> None:
    """这一步说的话记成名下在跑的委派的旁注（最近一句盖掉上一句、带时刻），不刷新进展计时；
    还没开跑的、空话不记；没接任务基建时什么都不做。"""
    ledger = TaskLedger()
    for task_id in ("d-1", "d-2"):
        ledger.register(
            task_id=task_id,
            provider="framework",
            tool="framework_delegate",
            initiator="streamer",
            executor="minecraft",
            source="agent",
            snapshot={"instruction": "把门装上"},
        )
    book = DelegationBook(task_tracker=SimpleNamespace(ledger=ledger), agent_name="minecraft")
    book.absorb("d-1")
    book.mark_running()
    book.absorb("d-2")
    before = ledger.get("d-1").updated_at_ms

    book.note_reply("先补木板", 100)
    book.note_reply("  门框左边差一格木板 ", 200)
    book.note_reply("   ", 300)

    record = ledger.get("d-1")
    assert record.snapshot == {
        "instruction": "把门装上",
        "latest_reply": "门框左边差一格木板",
        "latest_reply_at_ms": 200,
    }
    assert record.updated_at_ms == before
    assert "latest_reply" not in ledger.get("d-2").snapshot, "还没开跑的委派不记"
    _book().note_reply("没接任务基建", 1)


def test_absorb_then_mark_running_writes_running_and_tracks_for_finish() -> None:
    """受理 → 批次开跑：写 running（两种措辞：新开始与恢复）并入终态追踪清单。"""
    ledger = _FakeLedger()
    book = _book(ledger)

    book.absorb("d-1")
    book.absorb("d-2")
    assert ledger.updates == [], "absorb 只登记清单，不写账"
    assert book.finished_ids == ()

    book.mark_running()
    assert [(u["task_id"], u["status"], u["summary"]) for u in ledger.updates] == [
        ("d-1", "running", "执行 Agent 已开始处理"),
        ("d-2", "running", "执行 Agent 已开始处理"),
    ]
    assert book.finished_ids == ("d-1", "d-2")


def test_mark_running_resumes_held_tasks_from_previous_batch() -> None:
    """挂起保留的清单在下一批 mark_running 时重写 running（主播回话续做）。"""
    ledger = _FakeLedger()
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()
    book.hold("escalation: 等主播")
    ledger.updates.clear()

    book.mark_running()
    assert [(u["task_id"], u["status"], u["summary"]) for u in ledger.updates] == [
        ("d-1", "running", "执行 Agent 已恢复原任务")
    ]


def test_finish_writes_terminal_status_and_clears_tracking() -> None:
    ledger = _FakeLedger()
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()

    book.finish("succeeded", summary="delivery: 做完了")
    assert ledger.updates[-1] == {
        "task_id": "d-1",
        "status": "succeeded",
        "summary": "delivery: 做完了",
    }
    assert book.finished_ids == ()
    book.finish("succeeded", summary="再交付一次")  # 清空后是 no-op
    assert len(ledger.updates) == 2


def test_hold_keeps_tracking_and_writes_waiting_snapshot() -> None:
    ledger = _FakeLedger()
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()
    ledger.updates.clear()

    book.hold("escalation: 缺木头")
    assert ledger.updates == [
        {
            "task_id": "d-1",
            "status": "waiting_for_decision",
            "summary": "escalation: 缺木头",
            "snapshot": {"waiting_for_instruction": True, "reason": "escalation: 缺木头"},
        }
    ]
    assert book.finished_ids == ("d-1",), "挂起保留清单，批重启时重写 running"


def test_hold_merges_snapshot_and_keeps_instruction() -> None:
    """待定夺快照在既有快照上合并：受理时写入的 instruction 摘要不被冲掉。"""
    ledger = _FakeLedger()
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()
    ledger.active["d-1"] = _FakeRecord(source="agent", executor="minecraft")
    ledger.active["d-1"].snapshot = {"instruction": "建一座木头房子"}
    ledger.updates.clear()

    book.hold("escalation: 等主播定夺")
    snapshot = ledger.updates[-1]["snapshot"]
    assert snapshot == {
        "instruction": "建一座木头房子",
        "waiting_for_instruction": True,
        "reason": "escalation: 等主播定夺",
    }


def test_hold_without_ledger_record_does_not_raise() -> None:
    """账面记录已不在（终态移除）时挂起不炸：按空快照起底只写本次标记。"""
    ledger = _FakeLedger()  # active 为空，get 返回 None
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()
    ledger.updates.clear()

    book.hold("escalation: 等主播")
    assert ledger.updates[-1]["snapshot"] == {
        "waiting_for_instruction": True,
        "reason": "escalation: 等主播",
    }


def test_cancel_removes_from_tracking_and_writes_cancelled() -> None:
    ledger = _FakeLedger()
    book = _book(ledger)
    book.absorb("d-1")
    book.mark_running()
    book.absorb("d-2")  # 本批新吸收、还没标 running
    ledger.updates.clear()

    assert book.cancel("d-1", summary="被取消（source=planner）") is True
    assert book.cancel("d-2", summary="被取消（source=planner）") is True
    assert book.cancel("d-1", summary="再撤") is False, "不在追踪清单返回 False"
    assert [(u["task_id"], u["status"]) for u in ledger.updates] == [
        ("d-1", "cancelled"),
        ("d-2", "cancelled"),
    ]
    assert book.finished_ids == ()


def test_reject_writes_cancelled_without_tracking() -> None:
    """未受理拒收（收工态 / 排队中被撤）：账面直写，不进追踪清单。"""
    ledger = _FakeLedger()
    book = _book(ledger)

    book.reject("d-9", summary="收工态拒收（下播即收工）")
    assert ledger.updates == [{"task_id": "d-9", "status": "cancelled", "summary": "收工态拒收（下播即收工）"}]
    assert book.finished_ids == ()
    assert book.cancel("d-9", summary="x") is False, "拒收过的任务不在追踪清单"


def test_settle_on_live_end_cancels_own_agent_tasks_and_clears_lists() -> None:
    ledger = _FakeLedger()
    ledger.active["d-1"] = _FakeRecord(source="agent", executor="minecraft")
    ledger.active["d-2"] = _FakeRecord(source="agent", executor="text_adv")  # 别的 Agent 的
    ledger.active["d-3"] = _FakeRecord(source="tool", executor="minecraft")  # 工具型的不动
    book = _book(ledger)
    book.absorb("d-4")
    book.mark_running()
    ledger.updates.clear()

    settled = book.settle_on_live_end()
    assert settled == 1
    assert [(u["task_id"], u["status"], u["summary"]) for u in ledger.updates] == [("d-1", "cancelled", "下播自动收尾")]
    assert book.finished_ids == () and book.cancel("d-4", summary="x") is False, "追踪清单随账面清空"


def test_no_tracker_degrades_to_no_op_and_keeps_lists_empty() -> None:
    """无任务基建降级：absorb 先记号、mark_running 清空，写入口全部 no-op。"""
    book = _book()  # task_tracker=None

    book.absorb("d-1")
    book.mark_running()
    assert book.finished_ids == ()
    book.finish("succeeded", summary="x")  # 不抛异常
    book.hold("x")
    book.reject("d-9", summary="x")
    assert book.cancel("d-1", summary="x") is False, "mark_running 已清空批清单"
    assert book.settle_on_live_end() == 0
