"""
ContextService 数据模型

定义对话消息和会话信息的数据结构。
"""

import time
from enum import Enum
from typing import List, Optional
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
    emotion: Optional[str] = Field(default=None, description="情绪标签（ASSISTANT 消息可选填充，值为 Emotion 枚举名）")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    message_id: str = Field(default_factory=lambda: str(uuid4()), description="唯一ID")


class SessionInfo(BaseModel):
    """会话信息"""

    session_id: str = Field(..., description="会话ID")
    created_at: float = Field(default_factory=time.time, description="创建时间")
    last_active: float = Field(default_factory=time.time, description="最后活跃时间")
    message_count: int = Field(default=0, description="消息数量")


class DialogueTurn(BaseModel):
    """一轮对话配对：观众消息组 + 主播回复（若有）。

    回灌时从 live_chat 按时间戳配对构造；现场直播时由 StreamerAgent
    逐条 add_message 累积。本模型仅定义数据形态，存储层仍以逐条消息
    为主存储形态（与 ContextService 的 L1 配对窗口定位一致）。
    """

    session_id: str = Field(..., description="会话ID")
    viewer_messages: List[str] = Field(default_factory=list, description="本轮的观众消息（按时间正序）")
    assistant_message: Optional[str] = Field(default=None, description="主播回复（可能尚未产生，即本轮为纯观众输入）")
    assistant_emotion: Optional[str] = Field(default=None, description="主播回复情绪标签（可选）")
    start_timestamp: float = Field(default_factory=time.time, description="轮次开始时间戳（第一条观众消息）")
    end_timestamp: float = Field(default_factory=time.time, description="轮次结束时间戳（主播回复或最后一条消息）")
