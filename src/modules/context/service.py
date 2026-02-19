"""
ContextService - 对话上下文管理服务

提供对话历史存储和多会话管理功能。

设计模式参考 LLMManager：
- __init__(): 构造函数，接收配置
- initialize(): 初始化服务
- cleanup(): 清理资源
"""

from typing import Any, Dict, List, Optional

from src.modules.context.config import ContextServiceConfig, StorageType
from src.modules.context.models import ConversationMessage, MessageRole, SessionInfo
from src.modules.context.storage.memory import MemoryStorage
from src.modules.logging import get_logger


class ContextService:
    """
    上下文服务 - 管理对话历史和多会话

    参考 LLMManager 的设计模式，作为 Core 层基础设施服务。

    生命周期：
        __init__() -> initialize() -> [使用 API] -> cleanup()

    职责：
        - 管理多个会话的对话历史
        - 提供历史查询和上下文构建 API
        - 支持会话隔离（通过 session_id）
        - 自动管理消息和会话数量限制

    使用示例：
        ```python
        # 创建服务
        context_service = ContextService()
        await context_service.initialize()

        # 添加消息
        await context_service.add_message(
            session_id="console_input",
            role=MessageRole.USER,
            content="你好",
        )

        # 获取历史
        history = await context_service.get_history("console_input", limit=10)

        # 构建 LLM 上下文
        context = await context_service.build_context("console_input")

        # 清理
        await context_service.cleanup()
        ```
    """

    def __init__(self, config: Optional[ContextServiceConfig] = None):
        """
        初始化 ContextService

        Args:
            config: 服务配置，如果为 None 则使用默认配置
        """
        self.config = config or ContextServiceConfig()
        self.logger = get_logger("ContextService")
        self._storage = None
        self._initialized = False

    async def initialize(self) -> None:
        """
        初始化服务

        创建存储实例，加载持久化数据（如果启用）。

        Raises:
            RuntimeError: 如果重复初始化
        """
        if self._initialized:
            self.logger.warning("ContextService 已经初始化，跳过重复初始化")
            return

        # 创建存储实例
        if self.config.storage_type == StorageType.MEMORY:
            self._storage = MemoryStorage(
                max_messages_per_session=self.config.max_messages_per_session,
                max_sessions=self.config.max_sessions,
            )
        else:
            # FileStorage 实现可选
            self.logger.warning(f"存储类型 {self.config.storage_type} 暂未实现，使用内存存储")
            self._storage = MemoryStorage(
                max_messages_per_session=self.config.max_messages_per_session,
                max_sessions=self.config.max_sessions,
            )

        self._initialized = True
        self.logger.info(
            f"ContextService 初始化完成 "
            f"(存储: {self.config.storage_type}, "
            f"最大消息数: {self.config.max_messages_per_session}, "
            f"最大会话数: {self.config.max_sessions})"
        )

    async def add_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
    ) -> ConversationMessage:
        """
        添加消息到会话

        Args:
            session_id: 会话ID（建议使用 normalized_message.source）
            role: 消息角色（SYSTEM, USER, ASSISTANT）
            content: 消息内容

        Returns:
            创建的 ConversationMessage 对象

        Raises:
            RuntimeError: 如果服务未初始化

        使用示例：
            ```python
            # 使用 normalized_message.source 作为 session_id
            session_id = normalized_message.source  # "console_input", "bili_danmaku"

            await context_service.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=normalized_message.text,
            )
            ```
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        message = ConversationMessage(
            session_id=session_id,
            role=role,
            content=content,
        )
        await self._storage.add_message(message)

        self.logger.debug(f"添加消息到会话 {session_id}: {role.value} - {content[:50]}...")

        return message

    async def get_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_timestamp: Optional[float] = None,
    ) -> List[ConversationMessage]:
        """
        获取会话历史

        Args:
            session_id: 会话ID
            limit: 返回的最大消息数（None 表示不限制）
            before_timestamp: 只返回此时间戳之前的消息（None 表示不限制）

        Returns:
            ConversationMessage 列表（按时间正序排列）

        Raises:
            RuntimeError: 如果服务未初始化

        使用示例：
            ```python
            # 获取最近 10 条消息
            history = await context_service.get_history(session_id, limit=10)

            # 获取特定时间之前的消息
            timestamp = time.time() - 3600  # 1小时前
            history = await context_service.get_history(session_id, before_timestamp=timestamp)
            ```
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        messages = await self._storage.get_messages(session_id, limit, before_timestamp)

        self.logger.debug(f"获取会话 {session_id} 的历史，共 {len(messages)} 条消息")

        return messages

    async def build_context(
        self,
        session_id: str,
        max_tokens: Optional[int] = None,
        include_system_prompt: bool = True,
    ) -> List[Dict[str, str]]:
        """
        构建 LLM 上下文（OpenAI messages 格式）

        Args:
            session_id: 会话ID
            max_tokens: 最大 token 数（暂未实现，预留参数）
            include_system_prompt: 是否包含系统提示

        Returns:
            OpenAI 格式的消息列表 [{"role": "user", "content": "..."}]

        Raises:
            RuntimeError: 如果服务未初始化

        使用示例：
            ```python
            # 构建完整上下文（包含系统提示）
            context = await context_service.build_context(session_id)

            # 构建不含系统提示的上下文
            context = await context_service.build_context(session_id, include_system_prompt=False)

            # 直接用于 LLM 调用
            response = await llm_manager.chat_messages(messages=context)
            ```
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        messages = await self._storage.get_messages(session_id)

        result = []
        for msg in messages:
            # 过滤系统消息（如果配置不包含）
            if msg.role == MessageRole.SYSTEM and not include_system_prompt:
                continue
            result.append(
                {
                    "role": msg.role.value,
                    "content": msg.content,
                }
            )

        self.logger.debug(f"构建会话 {session_id} 的上下文，共 {len(result)} 条消息")

        return result

    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """
        获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            SessionInfo 对象，如果会话不存在则返回 None

        Raises:
            RuntimeError: 如果服务未初始化
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        return await self._storage.get_session_info(session_id)

    async def list_sessions(
        self,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[SessionInfo]:
        """
        列出会话

        Args:
            active_only: 是否只返回活跃会话（1小时内有活动）
            limit: 返回的最大会话数（None 表示不限制）

        Returns:
            SessionInfo 列表（按最后活跃时间倒序排列）

        Raises:
            RuntimeError: 如果服务未初始化
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        sessions = await self._storage.list_sessions(active_only, limit)

        self.logger.debug(f"列出会话，共 {len(sessions)} 个（活跃: {active_only}）")

        return sessions

    async def clear_session(self, session_id: str) -> None:
        """
        清空会话历史（保留会话信息）

        Args:
            session_id: 会话ID

        Raises:
            RuntimeError: 如果服务未初始化
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        await self._storage.clear_session(session_id)

        self.logger.info(f"已清空会话 {session_id} 的历史")

    async def delete_session(self, session_id: str) -> None:
        """
        删除会话（包括会话信息和历史）

        Args:
            session_id: 会话ID

        Raises:
            RuntimeError: 如果服务未初始化
        """
        if not self._initialized:
            raise RuntimeError("ContextService 未初始化，请先调用 initialize()")

        await self._storage.delete_session(session_id)

        self.logger.info(f"已删除会话 {session_id}")

    async def cleanup(self) -> None:
        """
        清理资源

        保存持久化数据（如果启用），释放存储资源。
        """
        if self._storage:
            await self._storage.cleanup()

        self._initialized = False
        self.logger.info("ContextService 已清理")

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            包含服务状态的字典
        """
        return {
            "initialized": self._initialized,
            "storage_type": self.config.storage_type.value,
            "max_messages_per_session": self.config.max_messages_per_session,
            "max_sessions": self.config.max_sessions,
            "session_timeout_seconds": self.config.session_timeout_seconds,
            "enable_persistence": self.config.enable_persistence,
        }
