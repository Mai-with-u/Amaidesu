"""
消息 Schema

定义消息相关的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class MessageItem(BaseModel):
    """消息项"""

    id: str
    session_id: str
    role: str  # user/assistant/system
    content: str
    timestamp: float
    metadata: Dict[str, Any] = {}


class MessageListResponse(BaseModel):
    """消息列表响应（游标分页）"""

    messages: List[MessageItem]
    has_more: bool
    next_cursor: Optional[float] = None  # 下一页游标（时间戳）
    limit: int


class SessionInfo(BaseModel):
    """会话信息"""

    session_id: str
    created_at: float
    last_active: float
    message_count: int
    is_active: bool


class SessionListResponse(BaseModel):
    """会话列表响应"""

    sessions: List[SessionInfo]
    total: int
