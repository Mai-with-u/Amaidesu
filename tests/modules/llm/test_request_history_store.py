"""RequestHistoryManager 读面：llm_requests 分页筛选、统计、清理与无库退化；写侧经 observation 出账入口落库"""

from __future__ import annotations

import pytest

from src.modules.llm.observation import record_call_outcome
from src.modules.llm.request_history_manager import RequestHistoryManager
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def manager(store: SQLiteDatabase) -> RequestHistoryManager:
    return RequestHistoryManager(llm_repo=store.llm)


async def _record_outcome(
    store: SQLiteDatabase,
    request_id: str,
    *,
    ts_ms: int,
    model: str = "glm-x",
    success: bool = True,
    tokens: int = 10,
    reasoning_tokens: int | None = None,
    profile_name: str = "planner",
    request_params: dict | None = None,
) -> None:
    """经 observation 出账单一入口写一条请求明细（与引擎同路径）。"""
    usage = None
    if success:
        usage = {
            "prompt_tokens": tokens,
            "completion_tokens": tokens // 2,
            "total_tokens": tokens + tokens // 2,
        }
        if reasoning_tokens is not None:
            usage["reasoning_tokens"] = reasoning_tokens
    await record_call_outcome(
        store.llm,
        request_id=request_id,
        profile_name=profile_name,
        model_name=model,
        request_type="generate",
        model_prices={},
        success=success,
        error=None if success else "boom",
        response_content="回复" if success else None,
        latency_ms=123,
        timestamp_ms=ts_ms,
        request_params=request_params or {"messages": [{"role": "user", "parts": ["hi"]}]},
        usage=usage,
    )


async def test_records_persist_and_query_with_paging_filters_and_detail(store, manager) -> None:
    await _record_outcome(store, "req-0", ts_ms=1_000)
    await _record_outcome(store, "req-1", ts_ms=2_000, success=False)
    await _record_outcome(store, "req-2", ts_ms=3_000, model="glm-y", profile_name="replyer")

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
    params = {
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
    await _record_outcome(store, "req-v14", ts_ms=7_000, reasoning_tokens=3, request_params=params)

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
    await _record_outcome(store, "req-1", ts_ms=1_750_000_000_000)
    await _record_outcome(store, "req-2", ts_ms=1_790_000_000_000, success=False)

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
    await _record_outcome(store, "req-s1", ts_ms=1_000, tokens=100)
    await _record_outcome(store, "req-s2", ts_ms=2_000, model="glm-y", tokens=30)

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


async def test_without_repo_queries_return_empty() -> None:
    """未注入仓储（装配退化场景）：查询返回空结果，不提供替代查询语义。"""
    manager = RequestHistoryManager(llm_repo=None)
    history = await manager.get_history()
    assert history["total"] == 0 and history["records"] == []
    assert await manager.get_request_by_id("req-cache") is None
    assert (await manager.get_statistics())["total_requests"] == 0
    assert await manager.get_available_dates() == []
    assert await manager.get_available_models() == []
    assert await manager.get_request_stats_by_ids(["req-cache"]) == {}
    assert (await manager.clear_history(confirm=True))["success"] is False
