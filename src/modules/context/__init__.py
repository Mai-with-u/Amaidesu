"""
ContextService - 对话上下文管理服务

提供对话历史存储和多会话管理功能。
"""

from src.modules.context.config import (
    ContextServiceConfig,
    StorageType,
)
from src.modules.context.models import (
    ConversationMessage,
    MessageRole,
    SessionInfo,
)
from src.modules.context.service import ContextService

__all__ = [
    "ContextService",
    "ConversationMessage",
    "SessionInfo",
    "MessageRole",
    "ContextServiceConfig",
    "StorageType",
]
