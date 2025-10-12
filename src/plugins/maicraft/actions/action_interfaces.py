"""
动作接口定义模块

定义所有抽象动作的接口。
每个动作接口规定了参数和行为，但不包含具体实现。
具体实现由不同的工厂创建（如 Log 实现、MCP 实现等）。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class IAction(ABC):
    """
    动作基础接口，所有动作都应该继承此接口。
    """

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> bool:
        """
        执行动作。

        Args:
            params: 动作参数字典

        Returns:
            执行是否成功
        """
        pass

    @abstractmethod
    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        验证参数是否有效。

        Args:
            params: 要验证的参数字典

        Returns:
            参数是否有效
        """
        pass

    @abstractmethod
    def get_action_type(self) -> str:
        """
        获取动作类型标识。

        Returns:
            动作类型字符串（如 "chat", "attack"）
        """
        pass


class IChatAction(IAction):
    """
    聊天动作接口。

    参数：
        - message: str - 要发送的聊天消息
    """

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> bool:
        """
        执行聊天动作。

        Args:
            params: 必须包含 'message' 键

        Returns:
            执行是否成功
        """
        pass


class IAttackAction(IAction):
    """
    攻击动作接口。

    参数：
        - mob_name: str - 要攻击的生物名称
    """

    @abstractmethod
    async def execute(self, params: Dict[str, Any]) -> bool:
        """
        执行攻击动作。

        Args:
            params: 必须包含 'mob_name' 键

        Returns:
            执行是否成功
        """
        pass
