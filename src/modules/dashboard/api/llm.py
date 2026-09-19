"""
LLM 管理 API

提供 LLM 用量统计、用量趋势图表和请求历史的查询接口。
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.llm import (
    LLMHistoryListResponse,
    LLMHistoryStatisticsModelStats,
    LLMHistoryStatisticsResponse,
    LLMRequestHistoryResponse,
    LLMUsageStatsResponse,
    LLMUsageSummaryResponse,
    LLMUsageTrendsResponse,
    TokenUsageSchema,
)
from src.modules.dashboard.services.llm_stats import build_usage_trends, cache_hit_rate
from src.modules.llm.request_history_manager import get_global_request_history_manager

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/usage", response_model=Dict[str, LLMUsageStatsResponse])
async def get_all_models_usage(server: ServerDep) -> Dict[str, LLMUsageStatsResponse]:
    """获取所有模型的用量统计（SQLite ``llm_usage`` 聚合）。

    库空（冷启动）返回空对象而非报错；旧 JSON 账本历史不迁移，切换后从零计账。
    """
    llm_repo = server.llm_repo
    if llm_repo is None:
        return {}

    rows = await llm_repo.llm_usage_by_model()
    result: Dict[str, LLMUsageStatsResponse] = {}
    for row in rows:
        model_name = row.get("model_name") or "unknown"
        hit = int(row.get("cache_hit_tokens", 0))
        miss = int(row.get("cache_miss_tokens", 0))
        result[model_name] = LLMUsageStatsResponse(
            model_name=model_name,
            total_prompt_tokens=int(row.get("total_prompt_tokens", 0)),
            total_completion_tokens=int(row.get("total_completion_tokens", 0)),
            total_tokens=int(row.get("total_tokens", 0)),
            total_calls=int(row.get("total_calls", 0)),
            total_cost=float(row.get("total_cost", 0.0)),
            cache_hit_tokens=hit,
            cache_miss_tokens=miss,
            cache_hit_rate=cache_hit_rate(hit, miss),
            first_call_time=row.get("first_call_time"),
            last_call_time=row.get("last_call_time"),
            last_updated=row.get("last_updated"),
        )

    return result


@router.get("/usage/trends", response_model=LLMUsageTrendsResponse)
async def get_usage_trends(
    server: ServerDep,
    days: Annotated[int, Query(ge=1, le=365, description="回看天数")] = 30,
) -> LLMUsageTrendsResponse:
    """获取近 N 天逐日用量趋势（图表数据源）。

    日期对齐、缺失日期补零与聚合在 ``services.llm_stats.build_usage_trends``。
    """
    return await build_usage_trends(server.llm_repo, days)


@router.get("/usage/summary", response_model=LLMUsageSummaryResponse)
async def get_usage_summary(server: ServerDep) -> LLMUsageSummaryResponse:
    """获取总费用摘要（SQLite ``llm_usage`` 聚合；库空返回全零）。"""
    llm_repo = server.llm_repo
    if llm_repo is None:
        return LLMUsageSummaryResponse()

    summary = await llm_repo.llm_usage_summary()
    hit = int(summary.get("cache_hit_tokens", 0))
    miss = int(summary.get("cache_miss_tokens", 0))
    return LLMUsageSummaryResponse(
        total_cost=float(summary.get("total_cost", 0.0)),
        total_prompt_tokens=int(summary.get("total_prompt_tokens", 0)),
        total_completion_tokens=int(summary.get("total_completion_tokens", 0)),
        total_tokens=int(summary.get("total_tokens", 0)),
        total_calls=int(summary.get("total_calls", 0)),
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        cache_hit_rate=cache_hit_rate(hit, miss),
        model_count=int(summary.get("model_count", 0)),
    )


@router.get("/history", response_model=LLMHistoryListResponse)
async def get_history(
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 50,
    model_name: Annotated[Optional[str], Query(description="模型名称筛选")] = None,
    client_type: Annotated[Optional[str], Query(description="客户端类型筛选")] = None,
    start_time: Annotated[Optional[int], Query(description="开始时间（毫秒时间戳）")] = None,
    end_time: Annotated[Optional[int], Query(description="结束时间（毫秒时间戳）")] = None,
    success_only: Annotated[Optional[bool], Query(description="只返回成功的请求")] = None,
) -> LLMHistoryListResponse:
    """获取请求历史列表"""
    history_manager = get_global_request_history_manager()
    result = await history_manager.get_history(
        client_type=client_type,
        model_name=model_name,
        start_time=start_time,
        end_time=end_time,
        success_only=success_only,
        page=page,
        page_size=page_size,
    )

    items: List[LLMRequestHistoryResponse] = []
    for record in result.get("records", []):
        items.append(_convert_record_to_response(record))

    return LLMHistoryListResponse(
        items=items,
        total=result.get("total", 0),
        page=result.get("page", 1),
        page_size=result.get("page_size", 50),
        total_pages=result.get("total_pages", 0),
    )


@router.get("/history/dates", response_model=List[str])
async def get_available_dates() -> List[str]:
    """获取有记录的日期列表（降序）"""
    history_manager = get_global_request_history_manager()
    return await history_manager.get_available_dates()


@router.get("/history/statistics", response_model=LLMHistoryStatisticsResponse)
async def get_statistics(
    start_time: Annotated[Optional[int], Query(description="开始时间（毫秒时间戳）")] = None,
    end_time: Annotated[Optional[int], Query(description="结束时间（毫秒时间戳）")] = None,
) -> LLMHistoryStatisticsResponse:
    """获取历史统计信息"""
    history_manager = get_global_request_history_manager()
    stats = await history_manager.get_statistics(start_time=start_time, end_time=end_time)

    # 转换 model_stats
    model_stats: Dict[str, LLMHistoryStatisticsModelStats] = {}
    for model_name, model_data in stats.get("model_stats", {}).items():
        model_hit = model_data.get("cache_hit_tokens", 0)
        model_miss = model_data.get("cache_miss_tokens", 0)
        model_stats[model_name] = LLMHistoryStatisticsModelStats(
            count=model_data.get("count", 0),
            total_tokens=model_data.get("total_tokens", 0),
            total_cost=model_data.get("total_cost", 0.0),
            cache_hit_tokens=model_hit,
            cache_miss_tokens=model_miss,
            cache_hit_rate=cache_hit_rate(model_hit, model_miss),
        )

    return LLMHistoryStatisticsResponse(
        total_requests=stats.get("total_requests", 0),
        successful_requests=stats.get("successful_requests", 0),
        failed_requests=stats.get("failed_requests", 0),
        success_rate=stats.get("success_rate", 0.0),
        total_prompt_tokens=stats.get("total_prompt_tokens", 0),
        total_completion_tokens=stats.get("total_completion_tokens", 0),
        total_tokens=stats.get("total_tokens", 0),
        total_cost=stats.get("total_cost", 0.0),
        cache_hit_tokens=stats.get("cache_hit_tokens", 0),
        cache_miss_tokens=stats.get("cache_miss_tokens", 0),
        cache_hit_rate=cache_hit_rate(stats.get("cache_hit_tokens", 0), stats.get("cache_miss_tokens", 0)),
        avg_latency_ms=stats.get("avg_latency_ms", 0.0),
        model_stats=model_stats,
        client_stats=stats.get("client_stats", {}),
        time_range=stats.get("time_range"),
    )


@router.get("/history/{request_id}", response_model=Optional[LLMRequestHistoryResponse])
async def get_request_by_id(request_id: str) -> Optional[LLMRequestHistoryResponse]:
    """获取单个请求详情"""
    history_manager = get_global_request_history_manager()
    record = await history_manager.get_request_by_id(request_id)

    if not record:
        return None

    return _convert_record_to_response(record)


def _convert_record_to_response(record: Dict[str, Any]) -> LLMRequestHistoryResponse:
    """将请求记录字典转换为响应模型"""
    usage = None
    usage_data = record.get("usage")
    if usage_data:
        usage = TokenUsageSchema(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

    return LLMRequestHistoryResponse(
        request_id=record.get("request_id", ""),
        timestamp_ms=record.get("timestamp", 0),
        client_type=record.get("client_type", ""),
        model_name=record.get("model_name", ""),
        request_params=record.get("request_params", {}),
        response_content=record.get("response_content"),
        reasoning_content=record.get("reasoning_content"),
        tool_calls=record.get("tool_calls"),
        usage=usage,
        cost=record.get("cost", 0.0),
        success=record.get("success", True),
        error=record.get("error"),
        latency_ms=record.get("latency_ms", 0),
    )
