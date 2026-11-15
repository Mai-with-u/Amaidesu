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
