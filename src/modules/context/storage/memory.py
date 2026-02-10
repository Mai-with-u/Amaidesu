"""
内存存储实现
"""

import asyncio
import time
from typing import Dict, List, Optional

from src.modules.context.models import ConversationMessage, SessionInfo
from src.modules.context.storage import Storage
from src.modules.logging import get_logger


class MemoryStorage(Storage):
    """内存存储实现"""

    def __init__(
        self,
        max_messages_per_session: int = 100,
        max_sessions: int = 100,
    ):
        self.max_messages_per_session = max_messages_per_session
        self.max_sessions = max_sessions
        self._sessions: Dict[str, List[ConversationMessage]] = {}
        self._session_info: Dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()
        self.logger = get_logger("MemoryStorage")

    async def add_message(self, message: ConversationMessage) -> None:
        """添加消息到会话"""
        async with self._lock:
            session_id = message.session_id

            # 检查会话数限制
            if session_id not in self._sessions:
                if len(self._sessions) >= self.max_sessions:
                    # 删除最旧的会话
                    oldest_session = min(self._session_info.items(), key=lambda x: x[1].created_at)[0]
                    await self._delete_session_no_lock(oldest_session)
                    self.logger.debug(f"达到会话数限制，删除最旧会话: {oldest_session}")

                # 创建新会话
                self._sessions[session_id] = []
                self._session_info[session_id] = SessionInfo(session_id=session_id)

            # 检查消息数限制
            messages = self._sessions[session_id]
            if len(messages) >= self.max_messages_per_session:
                # 删除最旧的消息
                removed = messages.pop(0)
                self.logger.debug(f"会话 {session_id} 达到消息数限制，删除最旧消息: {removed.message_id}")

            # 添加新消息
            messages.append(message)

            # 更新会话信息
            session_info = self._session_info[session_id]
            session_info.message_count = len(messages)
            session_info.last_active = message.timestamp

    async def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_timestamp: Optional[float] = None,
    ) -> List[ConversationMessage]:
        """获取消息"""
        async with self._lock:
            if session_id not in self._sessions:
                return []

            messages = self._sessions[session_id].copy()

            # 时间过滤
            if before_timestamp is not None:
                messages = [m for m in messages if m.timestamp < before_timestamp]

            # 数量限制
            if limit is not None:
                messages = messages[-limit:]

            return messages

    async def clear_session(self, session_id: str) -> None:
        """清空会话"""
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].clear()
                self._session_info[session_id].message_count = 0
                self.logger.debug(f"已清空会话: {session_id}")

    async def delete_session(self, session_id: str) -> None:
        """删除会话"""
        async with self._lock:
            await self._delete_session_no_lock(session_id)

    async def _delete_session_no_lock(self, session_id: str) -> None:
        """内部方法：删除会话（无锁，必须在持有锁时调用）"""
        if session_id in self._sessions:
            del self._sessions[session_id]
        if session_id in self._session_info:
            del self._session_info[session_id]
        self.logger.debug(f"已删除会话: {session_id}")

    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        async with self._lock:
            return self._session_info.get(session_id)

    async def list_sessions(
        self,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[SessionInfo]:
        """列出会话"""
        async with self._lock:
            sessions = list(self._session_info.values())

            # 按最后活跃时间排序
            sessions.sort(key=lambda s: s.last_active, reverse=True)

            # 过滤活跃会话
            if active_only:
                now = time.time()
                # 假设1小时内活跃的会话为活跃会话
                sessions = [s for s in sessions if now - s.last_active < 3600]

            # 数量限制
            if limit is not None:
                sessions = sessions[:limit]

            return sessions

    async def cleanup(self) -> None:
        """清理资源"""
        async with self._lock:
            self._sessions.clear()
            self._session_info.clear()
