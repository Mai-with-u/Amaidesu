"""
ContextService 数据模型

定义对话消息和会话信息的数据结构。
"""

import time
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """消息角色"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ConversationMessage(BaseModel):
    """对话消息数据模型"""

    session_id: str = Field(..., description="会话ID")
    role: MessageRole = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    message_id: str = Field(default_factory=lambda: str(uuid4()), description="唯一ID")


class SessionInfo(BaseModel):
    """会话信息"""

    session_id: str = Field(..., description="会话ID")
    created_at: float = Field(default_factory=time.time, description="创建时间")
    last_active: float = Field(default_factory=time.time, description="最后活跃时间")
    message_count: int = Field(default=0, description="消息数量")
