"""
Log 版本的攻击动作实现

将攻击目标输出到日志，主要用于测试和调试。
"""

from src.utils.logger import get_logger
from ...interfaces import IAttackAction
from ...interfaces.attack_action import AttackActionParams


class LogAttackAction(IAttackAction):
    """
    Log 版本的攻击动作实现。
    将攻击目标输出到日志。
    """

    def __init__(self):
        self.logger = get_logger("LogAttackAction")

    def get_action_type(self) -> str:
        """获取动作类型"""
        return "attack"

    async def execute(self, params: AttackActionParams) -> bool:
        """
        执行攻击动作（输出到日志）。

        Args:
            params: AttackActionParams 类型，包含 mob 字段

        Returns:
            执行是否成功
        """
        if not self.validate_params(params):
            self.logger.error(f"攻击动作参数验证失败: {params}")
            return False

        mob = params["mob"]

        # 输出到日志
        self.logger.info(f"[MAICRAFT-ATTACK] 攻击生物: '{mob}'")

        return True
