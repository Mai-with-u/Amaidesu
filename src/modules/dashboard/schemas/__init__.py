"""
Dashboard Schema 模块

定义 API 请求和响应的数据模型。
"""

from src.modules.dashboard.schemas.debug import (
    EventBusStatsResponse,
    InjectMessageRequest,
    InjectMessageResponse,
)
from src.modules.dashboard.schemas.event import (
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
from src.modules.dashboard.schemas.agent import (
    AgentControlAction,
    AgentControlRequest,
    AgentControlResponse,
    AgentListResponse,
    AgentStateResponse,
    AgentSummary,
)
from src.modules.dashboard.schemas.rundown import (
    RundownConfigView,
    RundownControlAction,
    RundownControlRequest,
    RundownControlResponse,
    RundownSegmentView,
    RundownStateResponse,
)
from src.modules.dashboard.schemas.component import (
    ComponentControlAction,
    ComponentControlRequest,
    ComponentControlResponse,
    ComponentListResponse,
    ComponentSummary,
)
from src.modules.dashboard.schemas.event import (
    EventHistoryItem,
    EventListResponse,
    EventStatsResponse,
)
from src.modules.dashboard.schemas.session import (
    SessionActionResponse,
    SessionItem,
    SessionListResponse,
    SessionOpenRequest,
    SessionOpenResponse,
    SessionTimelineResponse,
)
from src.modules.dashboard.schemas.simulator import (
    GiftCreateRequest,
    GiftCreateResponse,
    GiftListResponse,
    GiftUpdateRequest,
    PersonaCreateRequest,
    PersonaCreateResponse,
    PersonaListResponse,
    PersonaUpdateRequest,
    ReplayDatesResponse,
    SimulatorOperationResponse,
    SimulatorRunStateResponse,
    SimulatorStartRequest,
    SimulatorStatusResponse,
)
from src.modules.dashboard.schemas.tool_catalog import (
    ProviderCard,
    ToolCatalogCategory,
    ToolCatalogResponse,
)
from src.modules.dashboard.schemas.system import (
    EventBusStats,
    GroupStatus,
    HealthResponse,
    SystemStatusResponse,
)

__all__ = [
    # System
    "EventBusStats",
    "GroupStatus",
    "SystemStatusResponse",
    "HealthResponse",
    # 组件
    "ComponentControlAction",
    "ComponentSummary",
    "ComponentListResponse",
    "ComponentControlRequest",
    "ComponentControlResponse",
    # Rundown
    "RundownControlAction",
    "RundownConfigView",
    "RundownSegmentView",
    "RundownStateResponse",
    "RundownControlRequest",
    "RundownControlResponse",
    # Debug
    "InjectMessageRequest",
    "InjectMessageResponse",
    "EventBusStatsResponse",
    # Event
    "WebSocketMessage",
    "SubscribeRequest",
    "SubscribeResponse",
    "EventHistoryItem",
    "EventListResponse",
    "EventStatsResponse",
    # 场次
    "SessionItem",
    "SessionListResponse",
    "SessionOpenRequest",
    "SessionOpenResponse",
    "SessionActionResponse",
    "SessionTimelineResponse",
    # 模拟器
    "SimulatorStartRequest",
    "SimulatorStatusResponse",
    "SimulatorRunStateResponse",
    "SimulatorOperationResponse",
    "ReplayDatesResponse",
    "PersonaCreateRequest",
    "PersonaUpdateRequest",
    "PersonaListResponse",
    "PersonaCreateResponse",
    "GiftCreateRequest",
    "GiftUpdateRequest",
    "GiftListResponse",
    "GiftCreateResponse",
    # 工具目录
    "ProviderCard",
    "ToolCatalogCategory",
    "ToolCatalogResponse",
    # Agent 控制面
    "AgentControlAction",
    "AgentControlRequest",
    "AgentControlResponse",
    "AgentListResponse",
    "AgentStateResponse",
    "AgentSummary",
    # LLM
    "TokenUsageSchema",
    "LLMUsageStatsResponse",
    "LLMUsageSummaryResponse",
    "LLMRequestHistoryResponse",
    "LLMHistoryListResponse",
    "LLMHistoryStatisticsModelStats",
    "LLMHistoryStatisticsResponse",
]
