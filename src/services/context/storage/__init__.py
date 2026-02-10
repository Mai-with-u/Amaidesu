"""
ContextService 存储层

定义存储抽象接口和实现。
"""

from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.context.models import ConversationMessage, SessionInfo


class Storage(ABC):
    """存储抽象基类"""

    @abstractmethod
    async def add_message(self, message: "ConversationMessage") -> None:
        """添加消息"""
        pass

    @abstractmethod
    async def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_timestamp: Optional[float] = None,
    ) -> List["ConversationMessage"]:
        """获取消息"""
        pass

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """清空会话"""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """删除会话"""
        pass

    @abstractmethod
    async def get_session_info(self, session_id: str) -> Optional["SessionInfo"]:
        """获取会话信息"""
        pass

    @abstractmethod
    async def list_sessions(
        self,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> List["SessionInfo"]:
        """列出会话"""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """清理资源"""
        pass
