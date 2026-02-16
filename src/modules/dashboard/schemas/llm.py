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


class LLMHistoryStatisticsResponse(BaseModel):
    """LLM 请求历史统计响应"""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    success_rate: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    avg_latency_ms: float = 0.0
    model_stats: Dict[str, LLMHistoryStatisticsModelStats] = {}
    client_stats: Dict[str, int] = {}
    time_range: Optional[Dict[str, Optional[int]]] = None
