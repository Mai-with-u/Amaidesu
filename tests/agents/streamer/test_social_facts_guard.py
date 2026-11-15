"""社交事实参考段的守护测试：仓储可用且在册事实存在时参考段数据非空、读取失败告警只发一次。

背景：`_recent_two_session_bounds` 曾把配置读在不存在的 ``self.config`` 上，
AttributeError 每轮被 Planner 吞掉，【游戏社交事实】整场为空。这里用真库 +
真 Agent 构造走通提供器链，防同类回归。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

from src.modules.events import EventBus
from src.modules.events.payloads.core import CoreErrorPayload
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.tasks import TaskLedger
from tests.agents.streamer.helpers import ActiveSessionStub, make_streamer_agent


@pytest.fixture()
async def store(tmp_path: Path):
    db = SQLiteDatabase(tmp_path / "guard.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _agent_with_live_game(store: SQLiteDatabase, **kwargs: Any):
    """在册一场 + 一个进行中的 minecraft 委派（使 _playing_games 判定在玩）。"""
    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    ledger = TaskLedger()
    ledger.register(
        task_id="t1",
        provider="minecraft",
        tool="do_thing",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent = make_streamer_agent(
        social_fact_repo=store.social_facts,
        archive_repo=store.archive,
        session_manager=ActiveSessionStub(active_pk=session),
        task_ledger=ledger,
        **kwargs,
    )
    return agent


async def test_load_social_facts_returns_seeded_facts(store) -> None:
    """仓储可用且有在册事实时，_load_social_facts 返回非空（防 AttributeError 类整场缺席回归）。"""
    await store.social_facts.insert_fact(kind="works", text="阿杰在修刷铁机", quote="修刷铁机", game="minecraft")
    agent = await _agent_with_live_game(store)
    facts = await agent._load_social_facts()
    assert len(facts) == 1
    assert facts[0].text == "阿杰在修刷铁机"


async def test_read_failure_emits_core_error_only_once(store) -> None:
    """读取失败：首个进程生命周期只发一条 core.error 正式告警，后续失败仅记日志不再弹窗。"""
    bus = EventBus(enable_stats=False)
    alerts: List[CoreErrorPayload] = []

    async def on_error(name: str, payload: CoreErrorPayload, source: str) -> None:
        alerts.append(payload)

    bus.on("core.error", on_error, CoreErrorPayload)

    class _BrokenRepo:
        async def list_active_facts(self, *, game: str, save_id: Any) -> List[Any]:
            raise RuntimeError("库炸了")

    session = await store.sessions.insert_live_session(started_at_ms=1_000, kind="live")
    ledger = TaskLedger()
    ledger.register(
        task_id="t1",
        provider="minecraft",
        tool="do_thing",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent = make_streamer_agent(
        social_fact_repo=_BrokenRepo(),
        session_manager=ActiveSessionStub(active_pk=session),
        task_ledger=ledger,
        event_bus=bus,
    )
    try:
        assert await agent._load_social_facts() == []
        assert await agent._load_social_facts() == []
        await bus.wait_until_drained()
        assert len(alerts) == 1
        assert alerts[0].data.get("source") == "streamer"
    finally:
        await bus.cleanup()
