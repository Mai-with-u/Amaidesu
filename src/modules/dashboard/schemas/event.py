"""
事件 Schema

定义 WebSocket 事件推送与事件历史查询端点的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventHistoryItem(BaseModel):
    """事件历史条目（对照 ``events.event_history.EventRecord`` 的序列化形状）。"""

    id: str = Field(description="事件唯一 ID（uuid4）")
    type: str = Field(description="事件类型名，如 room.message / planner.decision")
    event_name: str = Field(default="", description="EventBus 精确事件名；未知为空串")
    timestamp_ms: int = Field(description="事件时刻（Unix 毫秒）")
    level: str = Field(description="严重级别：info | warn | error")
    source: str = Field(description="数据源标识")
    summary: str = Field(default="", description="人类可读的一行摘要")
    data: Dict[str, Any] = Field(default_factory=dict, description="完整序列化载荷")


class EventListResponse(BaseModel):
    """事件历史查询响应（按时间倒序窗口，支持游标续传）。"""

    events: List[EventHistoryItem] = Field(default_factory=list)
    total: int = Field(default=0, description="本次返回条数")
    has_more: bool = Field(default=False, description="是否还有更早的事件（游标分页）")


class EventStatsResponse(BaseModel):
    """事件历史环形缓冲统计。"""

    total: int = Field(default=0, description="缓冲内当前事件数")
    capacity: int = Field(default=0, description="缓冲容量上限")
    by_type: Dict[str, int] = Field(default_factory=dict, description="按事件类型计数")
    by_level: Dict[str, int] = Field(default_factory=dict, description="按严重级别计数")
    by_source: Dict[str, int] = Field(default_factory=dict, description="按数据源计数")
    oldest_timestamp_ms: Optional[int] = Field(default=None, description="最早事件时刻（缓冲为空时为 null）")
    newest_timestamp_ms: Optional[int] = Field(default=None, description="最新事件时刻（缓冲为空时为 null）")


class WebSocketMessage(BaseModel):
    """WebSocket 消息格式"""

    kind: str = Field(
        default="event",
        description='消息类别："event"=EventBus 事件广播（进前端事件缓冲）；"stream"=观测流（'
        "如 thinking.delta，best-effort 直推，前端独立缓冲，不入事件通道）",
    )
    type: str
    timestamp_ms: int
    data: Dict[str, Any]
    id: Optional[str] = Field(
        default=None,
        description="事件唯一 ID（与事件历史 EventRecord.id 同源，前端幂等去重依据）；流消息为空",
    )


class SubscribeRequest(BaseModel):
    """订阅请求"""

    action: str  # "subscribe" | "unsubscribe"
    events: List[str]


class SubscribeResponse(BaseModel):
    """订阅响应"""

    success: bool
    subscribed_events: List[str]
    message: str
