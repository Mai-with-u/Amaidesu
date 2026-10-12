"""llm_usage 场次归属链路测试。

- 注入场次解析器后，落库的 ``llm_usage.live_session_id`` 取当前显式场次主键
- 无进行中场次（解析器返回 None）落 NULL = 场间消耗
- 解析器抛异常不阻断落库，该行落 NULL
- 未注入解析器时保持旧行为（落 NULL），兼容既有装配
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.llm.engine import LLMManager
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.schema import build_schema_sql


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        content="ok",
        reasoning_content=None,
        tool_calls=[],
        model="m1",
        request_id="rid-1",
        error=None,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        usage_raw_json=None,
    )


async def _persist(manager: LLMManager, request_id: str, profile_name: str = "minecraft") -> None:
    await manager._persist_llm_call(
        request_id=request_id,
        profile_name=profile_name,
        model_name="m1",
        method="generate",
        result=_result(),  # type: ignore[arg-type]
        kwargs={},
        duration_ms=5,
    )


async def _rows(conn_manager: SQLiteConnectionManager) -> list[dict[str, Any]]:
    with conn_manager.transaction() as db:
        return [dict(r) for r in db.execute("SELECT live_session_id, total_tokens FROM llm_usage").fetchall()]


@pytest.mark.asyncio
async def test_usage_attributed_to_active_session(tmp_path) -> None:
    """场内调用归属当前场次主键。"""
    conn = SQLiteConnectionManager(tmp_path / "t1.db")
    conn.connection().executescript(build_schema_sql())
    repo = LLMRepo(conn)
    manager = LLMManager(llm_repo=repo, live_session_resolver=lambda: 42)
    await _persist(manager, "r1")
    rows = await _rows(conn)
    assert len(rows) == 1
    assert rows[0]["live_session_id"] == 42


@pytest.mark.asyncio
async def test_usage_between_sessions_is_null(tmp_path) -> None:
    """无进行中场次（场间消耗）落 NULL，与存储惯例对齐。"""
    conn = SQLiteConnectionManager(tmp_path / "t2.db")
    conn.connection().executescript(build_schema_sql())
    repo = LLMRepo(conn)
    manager = LLMManager(llm_repo=repo, live_session_resolver=lambda: None)
    await _persist(manager, "r2", profile_name="planner")
    rows = await _rows(conn)
    assert len(rows) == 1
    assert rows[0]["live_session_id"] is None


@pytest.mark.asyncio
async def test_resolver_failure_falls_back_to_null(tmp_path) -> None:
    """归属解析失败只告警，落 NULL，不阻断落库。"""

    def _boom() -> int:
        raise RuntimeError("resolver down")

    conn = SQLiteConnectionManager(tmp_path / "t3.db")
    conn.connection().executescript(build_schema_sql())
    repo = LLMRepo(conn)
    manager = LLMManager(llm_repo=repo, live_session_resolver=_boom)
    await _persist(manager, "r3", profile_name="planner")
    rows = await _rows(conn)
    assert len(rows) == 1
    assert rows[0]["live_session_id"] is None


@pytest.mark.asyncio
async def test_no_resolver_keeps_null(tmp_path) -> None:
    """未注入解析器时保持旧行为（落 NULL），既有装配不受影响。"""
    conn = SQLiteConnectionManager(tmp_path / "t4.db")
    conn.connection().executescript(build_schema_sql())
    repo = LLMRepo(conn)
    manager = LLMManager(llm_repo=repo)
    await _persist(manager, "r4", profile_name="planner")
    rows = await _rows(conn)
    assert len(rows) == 1
    assert rows[0]["live_session_id"] is None
