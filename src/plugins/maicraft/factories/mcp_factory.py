"""
MCP 动作工厂

创建 MCP 系列的动作实现。
这些动作通过 MCP Server 执行真实的 Minecraft 操作。
"""

from src.utils.logger import get_logger
from .abstract_factory import AbstractActionFactory
from ..actions.action_interfaces import IChatAction, IAttackAction
from ..actions.impl.mcp import McpChatAction, McpAttackAction


class McpActionFactory(AbstractActionFactory):
    """
    MCP 动作工厂实现。

    创建一整套 MCP 版本的动作对象。
    """

    def __init__(self):
        self.logger = get_logger("McpActionFactory")
        self.initialized = False
        # TODO: 添加 MCP Server 连接配置
        self.mcp_server_url = None
        self.mcp_client = None

    def create_chat_action(self) -> IChatAction:
        """
        创建 MCP 版本的聊天动作。

        Returns:
            McpChatAction 实例
        """
        action = McpChatAction()
        # TODO: 将 MCP 客户端注入到动作中
        # action.mcp_client = self.mcp_client
        return action

    def create_attack_action(self) -> IAttackAction:
        """
        创建 MCP 版本的攻击动作。

        Returns:
            McpAttackAction 实例
        """
        action = McpAttackAction()
        # TODO: 将 MCP 客户端注入到动作中
        # action.mcp_client = self.mcp_client
        return action

    async def initialize(self) -> bool:
        """
        初始化 MCP 工厂。

        包括建立 MCP Server 连接等操作。

        Returns:
            初始化是否成功
        """
        try:
            # TODO: 实现 MCP Server 连接初始化
            # self.mcp_client = await connect_to_mcp_server(self.mcp_server_url)

            self.logger.info("MCP 动作工厂初始化完成")
            self.logger.warning("[TODO] MCP Server 连接逻辑待实现")
            self.initialized = True
            return True

        except Exception as e:
            self.logger.error(f"MCP 工厂初始化失败: {e}", exc_info=True)
            return False

    async def cleanup(self):
        """清理 MCP 工厂资源，包括断开 MCP Server 连接"""
        try:
            # TODO: 断开 MCP Server 连接
            # if self.mcp_client:
            #     await self.mcp_client.disconnect()

            self.logger.info("MCP 动作工厂清理完成")
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
