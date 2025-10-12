"""
MCP 版本的攻击动作实现

通过 MCP Server 在 Minecraft 中攻击指定生物。
目前为框架实现，具体的 MCP 调用逻辑需要根据实际 MCP Server 接口来完善。
"""

from __future__ import annotations  # ✅ 延迟类型注解求值

from src.utils.logger import get_logger
from ...interfaces import IAttackAction
from ...interfaces.attack_action import AttackActionParams
from ....mcp.client import global_mcp_client


class McpAttackAction(IAttackAction):
    """
    MCP 版本的攻击动作实现。
    通过 MCP Server 在 Minecraft 中攻击指定生物。
    """

    def __init__(self):
        self.logger = get_logger("McpAttackAction")
        self.mcp_client = global_mcp_client

    def get_action_type(self) -> str:
        """获取动作类型"""
        return "attack"

    async def execute(self, params: AttackActionParams) -> bool:
        """
        执行攻击动作（通过 MCP Server）。

        Args:
            params: AttackActionParams 类型，包含 mob 字段

        Returns:
            执行是否成功
        """
        if not self.validate_params(params):
            self.logger.error(f"攻击动作参数验证失败: {params}")
            return False

        mob = params["mob"]

        try:
            await self.mcp_client.call_tool_directly("attack", {"mob": mob})

            # 暂时返回 True，实际应该根据 MCP 调用结果返回
            return True

        except Exception as e:
            self.logger.error(f"MCP 攻击动作执行失败: {e}", exc_info=True)
            return False
