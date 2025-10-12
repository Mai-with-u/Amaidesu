"""
MCP 版本的聊天动作实现

通过 MCP Server 在 Minecraft 中发送聊天消息。
目前为框架实现，具体的 MCP 调用逻辑需要根据实际 MCP Server 接口来完善。
"""

from src.utils.logger import get_logger
from ...interfaces import IChatAction
from ...interfaces.chat_action import ChatActionParams
from ....mcp.client import global_mcp_client


class McpChatAction(IChatAction):
    """
    MCP 版本的聊天动作实现。
    通过 MCP Server 在 Minecraft 中发送聊天消息。
    """

    def __init__(self):
        self.logger = get_logger("McpChatAction")
        self.mcp_client = global_mcp_client

    def get_action_type(self) -> str:
        """获取动作类型"""
        return "chat"

    async def execute(self, params: ChatActionParams) -> bool:
        """
        执行聊天动作（通过 MCP Server）。

        Args:
            params: ChatActionParams 类型，包含 message 字段

        Returns:
            执行是否成功
        """
        if not self.validate_params(params):
            self.logger.error(f"聊天动作参数验证失败: {params}")
            return False

        message = params["message"]

        try:
            await self.mcp_client.call_tool_directly("chat", {"message": message})

            # 暂时返回 True，实际应该根据 MCP 调用结果返回
            return True

        except Exception as e:
            self.logger.error(f"MCP 聊天动作执行失败: {e}", exc_info=True)
            return False
