"""
ContextService 配置模型

定义上下文服务的配置结构。
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class StorageType(str, Enum):
    """存储类型"""

    MEMORY = "memory"
    FILE = "file"


class ContextServiceConfig(BaseModel):
    """ContextService 配置"""

    storage_type: StorageType = Field(default=StorageType.MEMORY, description="存储类型")
    storage_path: Optional[str] = Field(default=None, description="文件存储路径（可选）")
    max_messages_per_session: int = Field(default=100, description="每会话最大消息数")
    max_sessions: int = Field(default=100, description="最大会话数")
    session_timeout_seconds: float = Field(default=3600.0, description="会话超时时间（秒）")
    enable_persistence: bool = Field(default=False, description="启用持久化")
