"""
ContextService - 对话上下文管理服务

提供对话历史存储和多会话管理功能。
"""

from src.services.context.service import ContextService
from src.services.context.models import (
    ConversationMessage,
    SessionInfo,
    MessageRole,
)
from src.services.context.config import (
    ContextServiceConfig,
    StorageType,
)

__all__ = [
    "ContextService",
    "ConversationMessage",
    "SessionInfo",
    "MessageRole",
    "ContextServiceConfig",
    "StorageType",
]
