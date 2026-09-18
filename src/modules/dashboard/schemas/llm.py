"""
LLM 管理 Schema

定义 LLM 用量统计和请求历史相关的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class TokenUsageSchema(BaseModel):
    """Token 使用量"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMUsageStatsResponse(BaseModel):
    """单个模型的用量统计响应"""

    model_name: str
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_calls: int = 0
    total_cost: float = 0.0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # 缓存命中率（hit / (hit+miss)，0-1）；None 表示上游从未上报缓存用量
    cache_hit_rate: Optional[float] = None
    first_call_time: Optional[int] = None
    last_call_time: Optional[int] = None
    last_updated: Optional[int] = None


class LLMUsageSummaryResponse(BaseModel):
    """所有模型的总费用摘要响应"""

    total_cost: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_calls: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # 缓存命中率（hit / (hit+miss)，0-1）；None 表示上游从未上报缓存用量
    cache_hit_rate: Optional[float] = None
    model_count: int = 0


class LLMRequestHistoryResponse(BaseModel):
    """单个 LLM 请求历史记录响应"""

    request_id: str
    timestamp: int
    client_type: str
    model_name: str
    request_params: Dict[str, Any] = {}
    response_content: Optional[str] = None
    reasoning_content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[TokenUsageSchema] = None
    cost: float = 0.0
    success: bool = True
    error: Optional[str] = None
    latency_ms: int = 0


class LLMHistoryListResponse(BaseModel):
    """LLM 请求历史列表响应"""

    items: List[LLMRequestHistoryResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class LLMHistoryStatisticsModelStats(BaseModel):
    """按模型的统计"""

    count: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # 缓存命中率（0-1）；None 表示窗口内上游从未上报缓存用量
    cache_hit_rate: Optional[float] = None


class LLMHistoryStatisticsResponse(BaseModel):
    """LLM 请求历史统计响应"""

    # cache 两列：上游未上报缓存用量时入库记 0，因此 0 可能代表"未上报"而非真实零命中
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    success_rate: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    # 缓存命中率（0-1）；None 表示窗口内上游从未上报缓存用量
    cache_hit_rate: Optional[float] = None
    avg_latency_ms: float = 0.0
    model_stats: Dict[str, LLMHistoryStatisticsModelStats] = {}
    client_stats: Dict[str, int] = {}
    time_range: Optional[Dict[str, Optional[int]]] = None


class LLMUsageTrendPoint(BaseModel):
    """单日用量聚合点（补零对齐后的连续时间轴）"""

    date: str
    timestamp_ms: int
    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None


class LLMUsageTrendModelPoint(BaseModel):
    """单日单模型用量聚合点（仅有数据的日子）"""

    date: str
    model_name: str
    total_calls: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None


class LLMUsageTrendsResponse(BaseModel):
    """用量趋势响应（近 N 天逐日聚合）"""

    days: int
    points: List[LLMUsageTrendPoint] = []
    model_points: List[LLMUsageTrendModelPoint] = []
