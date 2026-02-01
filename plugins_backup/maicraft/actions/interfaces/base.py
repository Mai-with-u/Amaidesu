"""
动作基础接口

定义所有动作的共同基类。
"""

from abc import ABC, abstractmethod
from typing import Any, Mapping


class IAction(ABC):
    """
    动作基础接口，所有动作都应该继承此接口。
    """

    @abstractmethod
    async def execute(self, params: Mapping[str, Any]) -> bool:
        """
        执行动作。

        Args:
            params: 动作参数字典

        Returns:
            执行是否成功
        """
        pass

    def validate_params(self, params: Mapping[str, Any]) -> bool:
        """
        验证参数是否有效。

        此方法由 ValidatedAction 自动提供实现，
        子类通常不需要重写。

        Args:
            params: 要验证的参数字典

        Returns:
            参数是否有效
        """
        # 默认实现：不验证，总是返回 True
        # 实际的验证由 ValidatedAction 提供
        return True

    @abstractmethod
    def get_action_type(self) -> str:
        """
        获取动作类型标识。

        Returns:
            动作类型字符串（如 "chat", "attack"）
        """
        pass
