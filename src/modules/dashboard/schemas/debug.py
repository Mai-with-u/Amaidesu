"""
调试 Schema

定义调试相关的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


class InjectIntentRequest(BaseModel):
    """注入 Intent 请求"""

    text: str  # 对应 Intent.original_text
    response_text: Optional[str] = None  # 如果为空则使用 text
    emotion: str = "neutral"
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表，每个包含 type, params, priority")
    source: str = "dashboard_debug"


class InjectIntentResponse(BaseModel):
    """注入 Intent 响应"""

    success: bool
    intent_id: Optional[str] = None
    error: Optional[str] = None
