"""llm_requests 表 + RequestHistoryManager 落库/查询 单测"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.llm.request_history_manager import (
    RequestHistoryManager,
    RequestRecord,
    TokenUsage,
)
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="llm-request-history-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_dir: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_dir / "llm-history.db")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def manager(store: SQLiteStore) -> AsyncGenerator[RequestHistoryManager, None]:
    m = RequestHistoryManager(use_global=False, sqlite_store=store)
    yield m


def _record(request_id: str, *, ts_ms: int, model: str = "glm-x", success: bool = True, tokens: int = 10) -> RequestRecord:
    return RequestRecord(
        request_id=request_id,
        timestamp=ts_ms,
        client_type="llm",
        model_name=model,
        request_params={"messages": [{"role": "user", "content": "hi"}]},
        response_content="回复" if success else None,
        usage=TokenUsage(prompt_tokens=tokens, completion_tokens=tokens // 2, total_tokens=tokens + tokens // 2)
        if success
        else None,
        cost=0.01,
        success=success,
        error=None if success else "boom",
        latency_ms=123,
    )


async def _wait_for_count(store: SQLiteStore, expected: int, timeout: float = 3.0) -> None:
    async def _poll() -> None:
        while True:
            rows = await store.execute("SELECT COUNT(*) AS n FROM llm_requests")
            if int(rows[0]["n"]) >= expected:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


@pytest.mark.asyncio
async def test_record_request_persists_to_store(store: SQLiteStore) -> None:
    """record_request 经 fire-and-forget 写入 llm_requests 表。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-1", ts_ms=1_000))
    await _wait_for_count(store, 1)

    rows = await store.execute("SELECT * FROM llm_requests")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-1"
    assert row["total_tokens"] == 15
    assert row["success"] == 1


@pytest.mark.asyncio
async def test_get_history_from_store_pagination(store: SQLiteStore) -> None:
    """get_history 走 SQL 分页（时间倒序），记录字段还原完整。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    for idx in range(3):
        manager.record_request(_record(f"req-{idx}", ts_ms=1_000 * (idx + 1)))
    await _wait_for_count(store, 3)

    page = await manager.get_history(page=1, page_size=2)
    assert page["total"] == 3
    assert page["total_pages"] == 2
    assert [r["request_id"] for r in page["records"]] == ["req-2", "req-1"]

    first = page["records"][0]
    assert first["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert first["request_params"] == {"messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_get_history_filters(store: SQLiteStore) -> None:
    """模型/成功状态筛选生效。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-ok", ts_ms=1_000))
    manager.record_request(_record("req-bad", ts_ms=2_000, success=False))
    await _wait_for_count(store, 2)

    ok_only = await manager.get_history(success_only=True)
    assert [r["request_id"] for r in ok_only["records"]] == ["req-ok"]

    by_model = await manager.get_history(model_name="glm-x")
    assert by_model["total"] == 2


@pytest.mark.asyncio
async def test_get_request_by_id_from_store(store: SQLiteStore) -> None:
    """按 request_id 从库里取单条详情。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-detail", ts_ms=5_000))
    await _wait_for_count(store, 1)

    record = await manager.get_request_by_id("req-detail")
    assert record is not None and record["request_id"] == "req-detail"
    assert await manager.get_request_by_id("missing") is None


@pytest.mark.asyncio
async def test_get_statistics_from_store(store: SQLiteStore) -> None:
    """统计接口走 SQL 聚合，model_stats/client_stats 形状与旧实现一致。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-1", ts_ms=1_000))
    manager.record_request(_record("req-2", ts_ms=2_000, success=False))
    await _wait_for_count(store, 2)

    stats = await manager.get_statistics()
    assert stats["total_requests"] == 2
    assert stats["successful_requests"] == 1
    assert stats["failed_requests"] == 1
    assert stats["success_rate"] == pytest.approx(0.5)
    assert stats["total_tokens"] == 15  # 失败请求 usage 为空不计
    assert stats["model_stats"]["glm-x"]["count"] == 2
    assert stats["client_stats"]["llm"] == 2
    assert stats["time_range"] == {"start": None, "end": None}


@pytest.mark.asyncio
async def test_clear_history_from_store(store: SQLiteStore) -> None:
    """clear_history 删除库记录并修剪缓存。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-1", ts_ms=1_000))
    await _wait_for_count(store, 1)

    refused = await manager.clear_history(confirm=False)
    assert refused["success"] is False

    result = await manager.clear_history(confirm=True)
    assert result["success"] is True
    assert result["cleared_records"] == 1
    rows = await store.execute("SELECT COUNT(*) AS n FROM llm_requests")
    assert int(rows[0]["n"]) == 0


@pytest.mark.asyncio
async def test_available_dates_desc(store: SQLiteStore) -> None:
    """日期清单降序（最新在前）。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=store)
    manager.record_request(_record("req-1", ts_ms=1_750_000_000_000))  # 约某日
    manager.record_request(_record("req-2", ts_ms=1_790_000_000_000))  # 更晚日期
    await _wait_for_count(store, 2)

    dates = await manager.get_available_dates()
    assert len(dates) == 2
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_no_store_falls_back_to_cache() -> None:
    """未注入 store：查询退化为内存缓存路径，不报错。"""
    manager = RequestHistoryManager(use_global=False, sqlite_store=None)
    manager.record_request(_record("req-cache", ts_ms=1_000))
    assert manager.get_cache_size() == 1

    history = await manager.get_history()
    assert history["total"] == 1 and history["records"][0]["request_id"] == "req-cache"
    assert await manager.get_request_by_id("req-cache") is not None
    stats = await manager.get_statistics()
    assert stats["total_requests"] == 1
    assert await manager.get_available_dates() == []
