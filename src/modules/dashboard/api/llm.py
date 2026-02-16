"""
LLM 管理 API

提供 LLM 用量统计和请求历史的查询接口。
"""

from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.modules.dashboard.schemas.llm import (
    LLMHistoryListResponse,
    LLMHistoryStatisticsModelStats,
    LLMHistoryStatisticsResponse,
    LLMRequestHistoryResponse,
    LLMUsageStatsResponse,
    LLMUsageSummaryResponse,
    TokenUsageSchema,
)
from src.modules.llm.clients.token_usage_manager import get_global_token_manager
from src.modules.llm.request_history_manager import get_global_request_history_manager

router = APIRouter()


@router.get("/usage", response_model=Dict[str, LLMUsageStatsResponse])
async def get_all_models_usage() -> Dict[str, LLMUsageStatsResponse]:
    """获取所有模型的用量统计"""
    token_manager = get_global_token_manager()
    all_usage = token_manager.get_all_models_usage()

    result: Dict[str, LLMUsageStatsResponse] = {}
    for model_name, usage_data in all_usage.items():
        result[model_name] = LLMUsageStatsResponse(
            model_name=usage_data.get("model_name", model_name),
            total_prompt_tokens=usage_data.get("total_prompt_tokens", 0),
            total_completion_tokens=usage_data.get("total_completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            total_calls=usage_data.get("total_calls", 0),
            total_cost=usage_data.get("total_cost", 0.0),
            first_call_time=usage_data.get("first_call_time"),
            last_call_time=usage_data.get("last_call_time"),
            last_updated=usage_data.get("last_updated"),
        )

    return result


@router.get("/usage/summary", response_model=LLMUsageSummaryResponse)
async def get_usage_summary() -> LLMUsageSummaryResponse:
    """获取总费用摘要"""
    token_manager = get_global_token_manager()
    summary = token_manager.get_total_cost_summary()

    return LLMUsageSummaryResponse(
        total_cost=summary.get("total_cost", 0.0),
        total_prompt_tokens=summary.get("total_prompt_tokens", 0),
        total_completion_tokens=summary.get("total_completion_tokens", 0),
        total_tokens=summary.get("total_tokens", 0),
        total_calls=summary.get("total_calls", 0),
        model_count=summary.get("model_count", 0),
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
    result = history_manager.get_history(
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


@router.get("/history/{request_id}", response_model=Optional[LLMRequestHistoryResponse])
async def get_request_by_id(request_id: str) -> Optional[LLMRequestHistoryResponse]:
    """获取单个请求详情"""
    history_manager = get_global_request_history_manager()
    record = history_manager.get_request_by_id(request_id)

    if not record:
        return None

    return _convert_record_to_response(record)


@router.get("/history/dates", response_model=List[str])
async def get_available_dates() -> List[str]:
    """获取有记录的日期列表（降序）"""
    history_manager = get_global_request_history_manager()
    return history_manager.get_available_dates()


@router.get("/history/statistics", response_model=LLMHistoryStatisticsResponse)
async def get_statistics(
    start_time: Annotated[Optional[int], Query(description="开始时间（毫秒时间戳）")] = None,
    end_time: Annotated[Optional[int], Query(description="结束时间（毫秒时间戳）")] = None,
) -> LLMHistoryStatisticsResponse:
    """获取历史统计信息"""
    history_manager = get_global_request_history_manager()
    stats = history_manager.get_statistics(start_time=start_time, end_time=end_time)

    # 转换 model_stats
    model_stats: Dict[str, LLMHistoryStatisticsModelStats] = {}
    for model_name, model_data in stats.get("model_stats", {}).items():
        model_stats[model_name] = LLMHistoryStatisticsModelStats(
            count=model_data.get("count", 0),
            total_tokens=model_data.get("total_tokens", 0),
            total_cost=model_data.get("total_cost", 0.0),
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
        avg_latency_ms=stats.get("avg_latency_ms", 0.0),
        model_stats=model_stats,
        client_stats=stats.get("client_stats", {}),
        time_range=stats.get("time_range"),
    )


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
        timestamp=record.get("timestamp", 0),
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
