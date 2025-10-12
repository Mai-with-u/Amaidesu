"""
聊天动作接口

定义聊天动作的接口规范。
"""

from .base import IAction
from ..action_params import ChatActionParams
from ..param_validator import ValidatedAction


class IChatAction(IAction, ValidatedAction):
    """
    聊天动作接口。

    参数类型：ChatActionParams
        - message: str - 要发送的聊天消息

    使用 ValidatedAction 提供自动参数验证。
    """

    # ✅ 指定参数类型，自动获得验证功能
    PARAMS_TYPE = ChatActionParams

    async def execute(self, params: ChatActionParams) -> bool:
        """
        执行聊天动作。

        Args:
            params: ChatActionParams 类型，包含 message 字段

        Returns:
            执行是否成功
        """
        ...

    # ✅ validate_params 已经由 ValidatedAction 自动提供
    # 子类不需要再实现！
