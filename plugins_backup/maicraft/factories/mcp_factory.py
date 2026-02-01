"""
MCP 动作工厂

创建 MCP 系列的动作实现。
这些动作通过 MCP Server 执行真实的 Minecraft 操作。
"""

from typing import Optional
from src.utils.logger import get_logger
from .abstract_factory import AbstractActionFactory
from ..actions.interfaces import IChatAction, IAttackAction
from ..actions.impl.mcp import McpChatAction, McpAttackAction
from ..mcp.client import MCPClient


class McpActionFactory(AbstractActionFactory):
    """
    MCP 动作工厂实现。

    创建一整套 MCP 版本的动作对象。
    MCP Client 只在工厂初始化时才连接。
    """

    def __init__(self):
        self.logger = get_logger("McpActionFactory")
        self.initialized = False
        # ✅ MCP Client 在这里创建，但不连接
        self.mcp_client: Optional[MCPClient] = None

    def create_chat_action(self) -> IChatAction:
        """
        创建 MCP 版本的聊天动作。

        Returns:
            McpChatAction 实例
        """
        if not self.initialized or self.mcp_client is None:
            raise RuntimeError("MCP 工厂未初始化，请先调用 initialize()")

        action = McpChatAction()
        # ✅ 将已连接的 MCP 客户端注入到动作中
        action.mcp_client = self.mcp_client  # 此时 mcp_client 保证不为 None
        return action

    def create_attack_action(self) -> IAttackAction:
        """
        创建 MCP 版本的攻击动作。

        Returns:
            McpAttackAction 实例
        """
        if not self.initialized or self.mcp_client is None:
            raise RuntimeError("MCP 工厂未初始化，请先调用 initialize()")

        action = McpAttackAction()
        # ✅ 将已连接的 MCP 客户端注入到动作中
        action.mcp_client = self.mcp_client  # 此时 mcp_client 保证不为 None
        return action

    async def initialize(self) -> bool:
        """
        初始化 MCP 工厂。

        ✅ 只在这里才会连接 MCP Server！
        如果插件未启用或使用 log 工厂，此方法不会被调用。

        Returns:
            初始化是否成功
        """
        try:
            self.logger.info("开始初始化 MCP 工厂...")

            # ✅ 创建 MCP Client 实例
            self.mcp_client = MCPClient()

            # ✅ 连接到 MCP Server（只在这里连接！）
            self.logger.info("正在连接到 MCP Server...")
            connected = await self.mcp_client.connect(enable_auto_reconnect=True)

            if not connected:
                self.logger.error("MCP Server 连接失败")
                return False

            self.logger.info("✅ MCP Server 连接成功")
            self.initialized = True
            return True

        except Exception as e:
            self.logger.error(f"MCP 工厂初始化失败: {e}", exc_info=True)
            return False

    async def cleanup(self):
        """
        清理 MCP 工厂资源。

        ✅ 断开 MCP Server 连接
        """
        try:
            self.logger.info("开始清理 MCP 工厂...")

            # ✅ 断开 MCP Server 连接
            if self.mcp_client:
                self.logger.info("正在断开 MCP Server 连接...")
                await self.mcp_client.disconnect()
                self.mcp_client = None

            self.logger.info("✅ MCP 动作工厂清理完成")
            self.initialized = False

        except Exception as e:
            self.logger.error(f"MCP 工厂清理失败: {e}", exc_info=True)

    def get_factory_type(self) -> str:
        """
        获取工厂类型。

        Returns:
            "mcp"
        """
        return "mcp"
