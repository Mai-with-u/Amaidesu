"""RequestHistoryManager：llm_requests 落库（正文出库只留统计与预览）、分页筛选、统计、清理与无库缓存回退"""

from __future__ import annotations

import asyncio

import pytest

from src.modules.llm.request_history_manager import RequestHistoryManager, RequestRecord, TokenUsage
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def manager(store: SQLiteDatabase) -> RequestHistoryManager:
    return RequestHistoryManager(use_global=False, llm_repo=store.llm)


def _record(request_id: str, *, ts_ms: int, model: str = "glm-x", success: bool = True, tokens: int = 10, **kwargs):
    usage = TokenUsage(prompt_tokens=tokens, completion_tokens=tokens // 2, total_tokens=tokens + tokens // 2)
    return RequestRecord(
        request_id=request_id,
        timestamp=ts_ms,
        profile_name=kwargs.pop("profile_name", "planner"),
        model_name=model,
        request_params={"messages": [{"role": "user", "content": "hi"}]},
        response_content="回复" if success else None,
        usage=usage if success else None,
        cost=0.01,
        success=success,
        error=None if success else "boom",
        latency_ms=123,
        **kwargs,
    )


async def _wait_for_count(store: SQLiteDatabase, expected: int) -> None:
    """record_request 是 fire-and-forget 写入，轮询等落库。"""

    async def _poll() -> None:
        while (await store.execute("SELECT COUNT(*) AS n FROM llm_requests"))[0]["n"] < expected:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), 3.0)


async def test_records_persist_and_query_with_paging_filters_and_detail(store, manager) -> None:
    manager.record_request(_record("req-0", ts_ms=1_000))
    manager.record_request(_record("req-1", ts_ms=2_000, success=False))
    manager.record_request(_record("req-2", ts_ms=3_000, model="glm-y", profile_name="replyer"))
    await _wait_for_count(store, 3)

    row = (await store.execute("SELECT * FROM llm_requests WHERE request_id='req-0'"))[0]
    assert (row["total_tokens"], row["success"]) == (15, 1)

    page = await manager.get_history(page=1, page_size=2)
    assert (page["total"], page["total_pages"]) == (3, 2)
    assert [r["request_id"] for r in page["records"]] == ["req-2", "req-1"]  # 时间倒序
    first = page["records"][0]
    assert first["usage"] == first["usage"] | {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert "reasoning_tokens" in first["usage"]
    # 正文出库：列表只给 preview_text 摘要，不还原正文
    assert "request_params" not in first and first["preview_text"] == "hi"

    def ids(history) -> list[str]:
        return [r["request_id"] for r in history["records"]]

    assert ids(await manager.get_history(success_only=True)) == ["req-2", "req-0"]
    assert ids(await manager.get_history(model_name="glm-x")) == ["req-1", "req-0"]
    assert ids(await manager.get_history(profile_name="replyer")) == ["req-2"]
    assert (await manager.get_request_by_id("req-0"))["request_id"] == "req-0"
    assert await manager.get_request_by_id("missing") is None


async def test_body_fields_leave_db_keeping_statistics_and_preview(store, manager) -> None:
    """正文不入库（v17 起出库列已退役，正文在诊断文件）；profile_name / reasoning_tokens / preview_text 落库并还原。"""
    record = _record("req-v14", ts_ms=7_000).to_dict()
    record["request_params"] = {
        "messages": [
            {"role": "system", "content": "system prompt"},
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "帮我看看这局局势如何，下一步应该怎么走才稳妥，" * 3},
                    {"type": "image", "image": "data:image/jpeg;base64,xxx"},
                ],
            },
        ]
    }
    record["usage"]["reasoning_tokens"] = 3
    manager.record_request_from_dict(record)
    await _wait_for_count(store, 1)

    # 出库列已整列退役：表结构里不再有这些列
    columns = {row["name"] for row in await store.execute("PRAGMA table_info(llm_requests)")}
    for column in ("request_params", "reasoning_content", "tool_calls", "usage_raw_json", "breakdown_json"):
        assert column not in columns
    row = (await store.execute("SELECT * FROM llm_requests"))[0]
    assert (row["profile_name"], row["reasoning_tokens"]) == ("planner", 3)
    # 预览摘要取最后一条有正文的消息，截 50 字符
    assert row["preview_text"].startswith("帮我看看这局局势") and len(row["preview_text"]) <= 50
    # 注入截图计数写侧落库，列表行徽标唯一来源
    assert row["image_count"] == 1

    fetched = await manager.get_request_by_id("req-v14")
    assert (fetched["profile_name"], fetched["usage"]["reasoning_tokens"]) == ("planner", 3)
    assert "request_params" not in fetched and "usage_raw_json" not in fetched  # 详情正文走诊断文件回查
    first = (await manager.get_history())["records"][0]
    assert first["preview_text"] == row["preview_text"] and first["image_count"] == 1


async def test_statistics_dates_and_clear(store, manager) -> None:
    manager.record_request(_record("req-1", ts_ms=1_750_000_000_000))
    manager.record_request(_record("req-2", ts_ms=1_790_000_000_000, success=False))
    await _wait_for_count(store, 2)

    stats = await manager.get_statistics()
    assert (stats["total_requests"], stats["successful_requests"], stats["failed_requests"]) == (2, 1, 1)
    assert stats["success_rate"] == pytest.approx(0.5)
    assert stats["total_tokens"] == 15  # 失败请求无 usage 不计
    assert (stats["model_stats"]["glm-x"]["count"], stats["client_stats"]["planner"]) == (2, 2)
    assert stats["time_range"] == {"start": None, "end": None}

    dates = await manager.get_available_dates()
    assert len(dates) == 2 and dates == sorted(dates, reverse=True)

    assert (await manager.clear_history(confirm=False))["success"] is False
    result = await manager.clear_history(confirm=True)
    assert (result["success"], result["cleared_records"]) == (True, 2)
    assert (await store.execute("SELECT COUNT(*) AS n FROM llm_requests"))[0]["n"] == 0


async def test_request_stats_by_ids_from_store(store, manager) -> None:
    manager.record_request(_record("req-s1", ts_ms=1_000, tokens=100))
    manager.record_request(_record("req-s2", ts_ms=2_000, model="glm-y", tokens=30))
    await _wait_for_count(store, 2)

    stats = await manager.get_request_stats_by_ids(["req-s1", "req-s1", "req-s2", "missing"])
    assert stats == {
        "req-s1": {
            "model_name": "glm-x",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
        },
        "req-s2": stats["req-s2"],
    }
    assert (stats["req-s2"]["model_name"], stats["req-s2"]["prompt_tokens"]) == ("glm-y", 30)
    assert await manager.get_request_stats_by_ids([]) == {}


async def test_without_store_queries_fall_back_to_memory_cache() -> None:
    manager = RequestHistoryManager(use_global=False, llm_repo=None)
    manager.record_request(_record("req-cache", ts_ms=1_000, tokens=8))
    assert manager.get_cache_size() == 1

    history = await manager.get_history()
    assert history["total"] == 1 and history["records"][0]["request_id"] == "req-cache"
    assert await manager.get_request_by_id("req-cache") is not None
    assert (await manager.get_statistics())["total_requests"] == 1
    assert await manager.get_available_dates() == []
    stats = await manager.get_request_stats_by_ids(["req-cache", "missing"])
    assert list(stats) == ["req-cache"]
    assert (stats["req-cache"]["prompt_tokens"], stats["req-cache"]["completion_tokens"]) == (8, 4)
