"""llm_usage 场次归属：取解析器给出的显式场次主键；无场次、解析失败、未注入解析器都落 NULL"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.modules.llm.engine import LLMManager
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.schema import build_schema_sql


def _boom() -> int:
    raise RuntimeError("resolver down")


@pytest.mark.parametrize(
    ("resolver", "expected"),
    [(lambda: 42, 42), (lambda: None, None), (_boom, None), ("absent", None)],
    ids=["active", "between_sessions", "resolver_fails", "no_resolver"],
)
async def test_usage_live_session_attribution(tmp_path, resolver, expected) -> None:
    conn = SQLiteConnectionManager(tmp_path / "t.db")
    conn.connection().executescript(build_schema_sql())
    kwargs = {} if resolver == "absent" else {"live_session_resolver": resolver}
    manager = LLMManager(llm_repo=LLMRepo(conn), **kwargs)
    result = SimpleNamespace(
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
    await manager._persist_llm_call(
        request_id="r1",
        profile_name="planner",
        model_name="m1",
        method="generate",
        result=result,
        kwargs={},
        duration_ms=5,
    )
    with conn.transaction() as db:
        rows = [tuple(r) for r in db.execute("SELECT live_session_id, total_tokens FROM llm_usage").fetchall()]
    assert rows == [(expected, 15)]
