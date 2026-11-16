"""续播恢复：上一进程在途委派重登记进任务记录表（轻量半补）。

委派台账（TaskLedger）纯内存态，进程重启后整本清空；库里的委派账在启动期
把没等到终态的受理记成"被打断"（interrupted）。续播被选中的场次把这些行
重登记回台账并标注来源，收尾时不做。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.tasks import TaskLedger
from tests.agents.streamer.helpers import make_streamer_agent


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "restore.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _agent(store: SQLiteDatabase, ledger: TaskLedger):
    return make_streamer_agent(task_ledger=ledger, event_repo=store.events)


async def _seed_interrupted(store: SQLiteDatabase, session_id: int, task_id: str, *, instruction: str) -> None:
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="minecraft",
        task_id=task_id,
        kind="accepted",
        text=instruction,
        timestamp_ms=1_000,
        initiator="streamer",
    )
    await store.events.insert_delegation_event(
        live_session_id=session_id,
        game="minecraft",
        task_id=task_id,
        kind="interrupted",
        text="进程重启打断，做到哪不确定",
        timestamp_ms=2_000,
        initiator="streamer",
    )


async def test_resume_restores_interrupted_delegations_into_ledger(store) -> None:
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await _seed_interrupted(store, session, "deleg_a", instruction="修一下刷铁机")
    await _seed_interrupted(store, session, "deleg_b", instruction="给苦力怕盖棚")
    # 已有正常终态的委派不恢复
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="minecraft",
        task_id="deleg_done",
        kind="accepted",
        text="搭桥",
        timestamp_ms=1_100,
        initiator="streamer",
    )
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="minecraft",
        task_id="deleg_done",
        kind="succeeded",
        text="桥通了",
        timestamp_ms=1_200,
        initiator="streamer",
    )

    ledger = TaskLedger()
    agent = _agent(store, ledger)
    await agent._restore_interrupted_delegations_to_ledger(session)

    restored: Dict[str, Any] = {tid: ledger.get(tid) for tid in ("deleg_a", "deleg_b")}
    assert all(restored.values()), "被打断的在途委派应重登记进台账"
    for record in restored.values():
        assert record.executor == "minecraft"
        assert record.snapshot.get("restored") is True
        assert record.snapshot.get("note") == "恢复自上一进程（重启中断）"
        assert record.snapshot.get("instruction")
    assert ledger.get("deleg_done") is None, "已有终态的委派不重登记"
    # 同一执行者下最早那件恢复为在做（accepted），排在后面的恢复为待办（queued），不把排队单提前激活
    assert restored["deleg_a"].status == "accepted"
    assert restored["deleg_b"].status == "queued"
    assert restored["deleg_b"].snapshot.get("backlog_no") == 1


async def test_resume_preserves_explicit_queued_delegations_in_order_without_activating(store: SQLiteDatabase) -> None:
    """重启前处于 queued 排队的委派（无论前面有无在做单）：按 queued 排队顺序恢复，绝不误标为 accepted，
    且认领进手头在做视图的只有真正在做的那一件。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    # minecraft：deleg_run 在做，deleg_q1 与 deleg_q2 在排队等待
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="minecraft",
        task_id="deleg_run",
        kind="accepted",
        text="盖双层木屋",
        timestamp_ms=1_100,
        initiator="planner-react",
    )
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="minecraft",
        task_id="deleg_q1",
        kind="queued",
        text="开一块菜地",
        timestamp_ms=1_200,
        initiator="planner-react",
    )
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="minecraft",
        task_id="deleg_q2",
        kind="queued",
        text="修一下水车",
        timestamp_ms=1_300,
        initiator="planner-react",
    )
    # text_adv：只有一件原处于 queued 的任务（例如搁下回待办后重启）
    await store.events.insert_delegation_event(
        live_session_id=session,
        game="text_adv",
        task_id="deleg_only_queued",
        kind="queued",
        text="读支线档案",
        timestamp_ms=1_400,
        initiator="planner-react",
    )
    await store.events.settle_interrupted_delegations(
        text="重启前在做，被打断了，做到哪不确定",
        timestamp_ms=2_000,
        accepted_before_ms=3_000,
    )

    ledger = TaskLedger()
    agent = _agent(store, ledger)
    await agent._restore_interrupted_delegations_to_ledger(session)
    await agent._restore_interrupted_delegations(session)

    run_rec = ledger.get("deleg_run")
    q1_rec = ledger.get("deleg_q1")
    q2_rec = ledger.get("deleg_q2")
    only_q_rec = ledger.get("deleg_only_queued")
    assert run_rec is not None and run_rec.status == "accepted"
    assert q1_rec is not None and q1_rec.status == "queued" and q1_rec.snapshot["backlog_no"] == 1
    assert q2_rec is not None and q2_rec.status == "queued" and q2_rec.snapshot["backlog_no"] == 2
    assert q1_rec.snapshot["backlog_seq"] < q2_rec.snapshot["backlog_seq"]
    assert only_q_rec is not None and only_q_rec.status == "queued"
    # 手头的事只认领真正重启前在做的盖木屋，不把后面排队的修水车或只排队的支线档案误当在做
    in_hand_delegations = agent._planner.workbench.delegations
    assert set(in_hand_delegations) == {"minecraft"}
    assert in_hand_delegations["minecraft"].task_id == "deleg_run"


async def test_resume_does_not_duplicate_existing_ledger_entries(store) -> None:
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await _seed_interrupted(store, session, "deleg_a", instruction="修一下刷铁机")

    ledger = TaskLedger()
    ledger.register(
        task_id="deleg_a",
        provider="minecraft",
        tool="do_thing",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent = _agent(store, ledger)
    await agent._restore_interrupted_delegations_to_ledger(session)
    # register 幂等（保留先登记），台账里仍是原登记而非恢复件
    record = ledger.get("deleg_a")
    assert record is not None and record.snapshot.get("restored") is None


async def test_restore_skipped_without_ledger_or_repo(store) -> None:
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    await _seed_interrupted(store, session, "deleg_a", instruction="修一下刷铁机")
    agent = make_streamer_agent(task_ledger=None, event_repo=store.events)
    await agent._restore_interrupted_delegations_to_ledger(session)  # 不抛即通过
