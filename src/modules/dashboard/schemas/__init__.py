"""
Dashboard Schema 模块

定义 API 请求和响应的数据模型。
"""

from src.modules.dashboard.schemas.config import (
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)
from src.modules.dashboard.schemas.config_schema import (
    ConfigFieldType,
    ConfigFieldSchema,
    ConfigGroupSchema,
    ConfigSchemaResponse,
    ConfigUpdateRequest as ConfigSchemaUpdateRequest,
    ConfigUpdateResponse as ConfigSchemaUpdateResponse,
    ValidationRule,
)
from src.modules.dashboard.schemas.debug import (
    EventBusStatsResponse,
    InjectIntentRequest,
    InjectIntentResponse,
    InjectMessageRequest,
    InjectMessageResponse,
)
from src.modules.dashboard.schemas.event import (
    ClientInfo,
    SubscribeRequest,
    SubscribeResponse,
    WebSocketMessage,
)
from src.modules.dashboard.schemas.llm import (
    LLMHistoryListResponse,
    LLMHistoryStatisticsModelStats,
    LLMHistoryStatisticsResponse,
    LLMRequestHistoryResponse,
    LLMUsageStatsResponse,
    LLMUsageSummaryResponse,
    TokenUsageSchema,
)
from src.modules.dashboard.schemas.message import (
    MessageItem,
    MessageListResponse,
    SessionInfo,
    SessionListResponse,
)
from src.modules.dashboard.schemas.provider import (
    ProviderControlAction,
    ProviderControlRequest,
    ProviderControlResponse,
    ProviderDetail,
    ProviderDetailResponse,
    ProviderListResponse,
    ProviderSummary,
)
from src.modules.dashboard.schemas.system import (
    DomainStatus,
    EventStats,
    HealthResponse,
    SystemStatsResponse,
    SystemStatusResponse,
)

__all__ = [
    # System
    "EventStats",
    "DomainStatus",
    "SystemStatusResponse",
    "SystemStatsResponse",
    "HealthResponse",
    # Provider
    "ProviderControlAction",
    "ProviderSummary",
    "ProviderDetail",
    "ProviderListResponse",
    "ProviderDetailResponse",
    "ProviderControlRequest",
    "ProviderControlResponse",
    # Message
    "MessageItem",
    "MessageListResponse",
    "SessionInfo",
    "SessionListResponse",
    # Config
    "ConfigResponse",
    "ConfigUpdateRequest",
    "ConfigUpdateResponse",
    # Config Schema
    "ConfigFieldType",
    "ConfigFieldSchema",
    "ConfigGroupSchema",
    "ConfigSchemaResponse",
    "ConfigSchemaUpdateRequest",
    "ConfigSchemaUpdateResponse",
    "ValidationRule",
    # Debug
    "InjectMessageRequest",
    "InjectMessageResponse",
    "InjectIntentRequest",
    "InjectIntentResponse",
    "EventBusStatsResponse",
    # Event
    "WebSocketMessage",
    "SubscribeRequest",
    "SubscribeResponse",
    "ClientInfo",
    # LLM
    "TokenUsageSchema",
    "LLMUsageStatsResponse",
    "LLMUsageSummaryResponse",
    "LLMRequestHistoryResponse",
    "LLMHistoryListResponse",
    "LLMHistoryStatisticsModelStats",
    "LLMHistoryStatisticsResponse",
]
