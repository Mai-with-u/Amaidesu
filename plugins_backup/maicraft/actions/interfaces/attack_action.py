"""
攻击动作接口

定义攻击动作的接口规范。
"""

from typing import TypedDict

from ..param_validator import ValidatedAction
from .base import IAction


class AttackActionParams(TypedDict):
    """
    攻击动作参数。

    Attributes:
        mob: 要攻击的生物名称
    """

    mob: str


class IAttackAction(IAction, ValidatedAction):
    """
    攻击动作接口。

    参数类型：AttackActionParams
        - mob: str - 要攻击的生物名称

    使用 ValidatedAction 提供自动参数验证。
    """

    # ✅ 指定参数类型，自动获得验证功能
    PARAMS_TYPE = AttackActionParams

    async def execute(self, params: AttackActionParams) -> bool:
        """
        执行攻击动作。

        Args:
            params: AttackActionParams 类型，包含 mob 字段

        Returns:
            执行是否成功
        """
        ...
