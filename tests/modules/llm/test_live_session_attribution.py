"""llm_usage 场次归属：取解析器给出的显式场次主键；无场次、解析失败、未注入解析器都落 NULL"""

from __future__ import annotations

import time

import pytest

from src.modules.llm.call_observer import CallObserver
from src.modules.llm.payload import Response, Usage
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
    observer = CallObserver(
        llm_repo=LLMRepo(conn),
        live_session_resolver=None if resolver == "absent" else resolver,
        diagnostics_writer=None,
        model_index={},
        model_prices={},
    )
    # 观测层契约是中立 payload.Response；usage 走 Usage 模型（provider 未上报项为 None）
    result = Response(
        success=True,
        content="ok",
        model="m1",
        request_id="rid-1",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    await observer.record_call(
        request_id="r1",
        profile_name="planner",
        model_name="m1",
        request_type="generate",
        result=result,
        kwargs={},
        start_time=time.time(),
    )
    with conn.transaction() as db:
        rows = [tuple(r) for r in db.execute("SELECT live_session_id, total_tokens FROM llm_usage").fetchall()]
        detail = [tuple(r) for r in db.execute("SELECT request_id, success, total_tokens FROM llm_requests").fetchall()]
    assert rows == [(expected, 15)]
    # 出账单一入口：聚合账之外同事务写请求明细行
    assert detail == [("r1", 1, 15)]
