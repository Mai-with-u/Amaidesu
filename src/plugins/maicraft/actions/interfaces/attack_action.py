"""
攻击动作接口

定义攻击动作的接口规范。
"""

from .base import IAction
from ..action_params import AttackActionParams
from ..param_validator import ValidatedAction


class IAttackAction(IAction, ValidatedAction):
    """
    攻击动作接口。

    参数类型：AttackActionParams
        - mob_name: str - 要攻击的生物名称

    使用 ValidatedAction 提供自动参数验证。
    """

    # ✅ 指定参数类型，自动获得验证功能
    PARAMS_TYPE = AttackActionParams

    async def execute(self, params: AttackActionParams) -> bool:
        """
        执行攻击动作。

        Args:
            params: AttackActionParams 类型，包含 mob_name 字段

        Returns:
            执行是否成功
        """
        ...

    # ✅ validate_params 已经由 ValidatedAction 自动提供
    # 子类不需要再实现！
