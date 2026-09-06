"""
调试 Schema

定义调试相关的数据模型。
"""

from typing import Dict, Optional

from pydantic import BaseModel


class InjectMessageRequest(BaseModel):
    """注入消息请求"""

    source: str = "debug_inject"
    text: str
    data_type: str = "text"
    importance: float = 0.5


class InjectMessageResponse(BaseModel):
    """注入消息响应"""

    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class EventBusStatsResponse(BaseModel):
    """EventBus 统计响应"""

    total_events: int = 0
    total_subscribers: int = 0
    events_by_name: Dict[str, int] = {}
