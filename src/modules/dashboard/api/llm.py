"""
LLM 管理 API

提供 LLM 用量统计、用量趋势图表和请求历史的查询接口。
"""

import asyncio
from typing import TYPE_CHECKING, Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.llm import (
    LLMContextBreakdownResponse,
    LLMHistoryListResponse,
    LLMHistoryStatisticsModelStats,
    LLMHistoryStatisticsResponse,
    LLMModelTestRequest,
    LLMModelTestResponse,
    LLMRequestHistoryListItem,
    LLMRequestHistoryResponse,
    LLMRequestStatsBatchRequest,
    LLMRequestStatsBatchResponse,
    LLMRequestStatsItem,
    LLMUsageStatsResponse,
    LLMUsageSummaryResponse,
    LLMUsageTrendsResponse,
)
from src.modules.dashboard.services.llm_stats import (
    build_context_breakdown_response,
    build_history_list_item,
    build_usage_trends,
    cache_hit_rate,
    convert_record_to_response,
    merge_payload_from_file,
)
from src.modules.dashboard.services.model_test import find_saved_provider, test_model_connectivity
from src.modules.llm.readers import (
    read_latest_breakdowns,
    read_request_breakdown,
)
from src.modules.llm.quota import ProviderQuota

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
    # 最近一次调用的输入 token：单独一条子查询拿到每模型最新一条记录的
    # prompt_tokens（不等同于 SUM 聚合，是水位分子）
    latest_prompt_tokens: Dict[str, int] = await llm_repo.llm_usage_latest_prompt_tokens()
    # 上下文窗口：装配期由 LLMManager 按 model_identifier 建索引；未注入
    # / 未配置时一律取 0，前端据此隐藏水位（不显示 0%）
    context_windows: Dict[str, int] = _context_windows_of(server)

    result: Dict[str, LLMUsageStatsResponse] = {}
    for row in rows:
        model_name = row.get("model_name") or "unknown"
        hit = int(row.get("cache_hit_tokens", 0))
        miss = int(row.get("cache_miss_tokens", 0))
        latest_prompt = latest_prompt_tokens.get(model_name)
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
            context_window=int(context_windows.get(model_name, 0)),
            last_call_prompt_tokens=latest_prompt,
        )

    return result


def _context_windows_of(server: "DashboardServer") -> Dict[str, int]:
    """取 LLMManager 装配期的每模型窗口索引（未注入/未配置一律空）。"""
    llm_manager = server.llm_manager
    return llm_manager.get_model_context_windows() if llm_manager is not None else {}


@router.get("/context-breakdown", response_model=Dict[str, LLMContextBreakdownResponse])
async def get_context_breakdown(server: ServerDep) -> Dict[str, LLMContextBreakdownResponse]:
    """每模型最近一次调用的上下文分段占用（"上下文水位"悬停明细数据源）。

    数据取每模型最新水位快照（``logs/llm_context/latest.json``，引擎成功
    路径覆写），窗口上限取 LLMManager 装配索引；快照空时返回空对象
    （分段占用只产生于引擎成功路径，快照缺条目 = 该模型尚无水位可看）。
    ``breakdown`` 解析失败的条目跳过（不伪造分段）。
    """
    context_windows = _context_windows_of(server)
    result: Dict[str, LLMContextBreakdownResponse] = {}

    snapshot_models = read_latest_breakdowns(server.diagnostics_writer)
    for model_name, breakdown in snapshot_models.items():
        row = {
            "timestamp_ms": breakdown.get("timestamp_ms"),
            "profile_name": breakdown.get("profile_name"),
            "request_id": breakdown.get("request_id"),
        }
        result[model_name] = build_context_breakdown_response(
            breakdown, model_name=model_name, row=row, context_windows=context_windows
        )
    return result


@router.get(
    "/context-breakdown/{request_id}",
    response_model=Optional[LLMContextBreakdownResponse],
)
async def get_context_breakdown_by_request(request_id: str, server: ServerDep) -> Optional[LLMContextBreakdownResponse]:
    """按请求 ID 取单次调用的上下文分段占用（时间线决策卡水位胶囊悬停明细）。

    分段占用存诊断文件（按库行 ``timestamp_ms`` 定位回查）；文件未命中
    （估算不可用的行）或行不存在一律返回 null（前端按空态渲染，不报错）；
    窗口上限同口径取 LLMManager 装配索引。
    """
    llm_repo = server.llm_repo
    if llm_repo is None:
        return None

    row = await llm_repo.get_llm_request_by_id(request_id)
    if row is None:
        return None

    # 文件回查线性扫描小时文件，挪线程池避免冻结事件循环（悬停触发，低频）
    breakdown = await asyncio.to_thread(
        read_request_breakdown, request_id, int(row.get("timestamp_ms") or 0), server.diagnostics_writer
    )
    if not isinstance(breakdown, dict):
        return None
    model_name = str(row.get("model_name") or "")
    return build_context_breakdown_response(
        breakdown, model_name=model_name, row=row, context_windows=_context_windows_of(server)
    )


@router.get("/quota", response_model=Optional[ProviderQuota])
async def get_provider_quota(
    server: ServerDep,
    model_name: Annotated[str, Query(min_length=1, description="API 模型标识（与用量统计的 model_name 同口径）")],
) -> Optional[ProviderQuota]:
    """模型所属 provider 的平台额度（上下文面板 Usage 区数据源）。

    模型标识走查询参数而非路径段：OpenRouter 等平台的标识含斜杠。平台不支持、
    未配置 Key、查询失败或未注入 LLMManager 时一律返回 null，前端隐藏 Usage 区。
    """
    service = server.provider_quota
    if service is None:
        return None
    return await service.get_quota_for_model(model_name)


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
    server: ServerDep,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 50,
    model_name: Annotated[Optional[str], Query(description="模型名称筛选")] = None,
    profile_name: Annotated[Optional[str], Query(description="用途筛选")] = None,
    start_time: Annotated[Optional[int], Query(description="开始时间（毫秒时间戳）")] = None,
    end_time: Annotated[Optional[int], Query(description="结束时间（毫秒时间戳）")] = None,
    success_only: Annotated[Optional[bool], Query(description="只返回成功的请求")] = None,
) -> LLMHistoryListResponse:
    """获取请求历史列表"""
    history_manager = server.request_history_manager
    result = await history_manager.get_history(
        profile_name=profile_name,
        model_name=model_name,
        start_time=start_time,
        end_time=end_time,
        success_only=success_only,
        page=page,
        page_size=page_size,
    )

    items: List[LLMRequestHistoryListItem] = []
    for record in result.get("records", []):
        items.append(build_history_list_item(record))

    return LLMHistoryListResponse(
        items=items,
        total=result.get("total", 0),
        page=result.get("page", 1),
        page_size=result.get("page_size", 50),
        total_pages=result.get("total_pages", 0),
    )


@router.get("/history/models", response_model=List[str])
async def get_available_models(server: ServerDep) -> List[str]:
    """获取历史记录中出现过的模型名（去重升序；供筛选下拉全量候选项）"""
    history_manager = server.request_history_manager
    return await history_manager.get_available_models()


@router.get("/history/statistics", response_model=LLMHistoryStatisticsResponse)
async def get_statistics(
    server: ServerDep,
    start_time: Annotated[Optional[int], Query(description="开始时间（毫秒时间戳）")] = None,
    end_time: Annotated[Optional[int], Query(description="结束时间（毫秒时间戳）")] = None,
) -> LLMHistoryStatisticsResponse:
    """获取历史统计信息"""
    history_manager = server.request_history_manager
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
async def get_request_by_id(request_id: str, server: ServerDep) -> Optional[LLMRequestHistoryResponse]:
    """获取单个请求详情"""
    history_manager = server.request_history_manager
    record = await history_manager.get_request_by_id(request_id)

    if not record:
        return None

    # 正文回查线性扫描小时文件（峰值 18.7MB/文件），同步跑在事件循环里会
    # 冻结整个 WebUI；挪线程池（浏览器逐卡展开时触发，低频）
    merged = await asyncio.to_thread(merge_payload_from_file, record, server.diagnostics_writer)
    return convert_record_to_response(merged)


@router.post("/models/test", response_model=LLMModelTestResponse)
async def test_model(request: LLMModelTestRequest, server: ServerDep) -> LLMModelTestResponse:
    """模型连通性测试（模型注册表"测试"按钮数据面）。

    前端只报提供商名 + 模型标识（非敏感字符串）；连接参数与密钥按名从
    已保存配置解析（改完先保存再测试），密钥全程不出服务端。提供商未
    保存属业务级失败，与密钥错误同以 ``ok=False`` + 原因返回。同步长请求
    （最长 timeout_ms），前端需放宽超时并做 loading 防连点。
    """
    provider_cfg = find_saved_provider(server.config_service, request.provider)
    if provider_cfg is None:
        return LLMModelTestResponse(ok=False, error=f"提供商「{request.provider}」尚未保存到配置，请先保存后再测试")
    return await test_model_connectivity(provider_cfg, request.model, request.timeout_ms)


@router.post("/history/stats", response_model=LLMRequestStatsBatchResponse)
async def get_request_stats_batch(
    request: LLMRequestStatsBatchRequest, server: ServerDep
) -> LLMRequestStatsBatchResponse:
    """按 id 批量取请求统计（仅库查询统计列，不含正文大字段）。

    时间线统计胶囊合批懒取专用：一次回看首屏有数百张带请求指针的卡，
    逐卡 GET /history/{id} 会回传整段请求正文并触发读侧诊断文件回查
    （统计胶囊只消费 usage/model 几个数），合批轻端点把 N 个重请求收敛
    为 1 个轻请求。
    """
    history_manager = server.request_history_manager
    stats = await history_manager.get_request_stats_by_ids(request.request_ids)
    return LLMRequestStatsBatchResponse(
        stats={request_id: LLMRequestStatsItem(**item) for request_id, item in stats.items()}
    )
