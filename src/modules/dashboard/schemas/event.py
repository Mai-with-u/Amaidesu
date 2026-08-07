"""
WebSocket 事件 Schema

定义 WebSocket 事件的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WebSocketMessage(BaseModel):
    """WebSocket 消息格式"""

    type: str
    timestamp: float
    data: Dict[str, Any]
    id: Optional[str] = Field(
        default=None,
        description="事件唯一 ID（与事件历史 EventRecord.id 同源，前端幂等去重依据）",
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


class ClientInfo(BaseModel):
    """客户端信息"""

    client_id: str
    connected_at: float
    subscribed_events: List[str]
