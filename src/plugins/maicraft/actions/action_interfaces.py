"""
动作接口定义模块

定义所有抽象动作的接口。
每个动作接口规定了参数和行为，但不包含具体实现。
具体实现由不同的工厂创建（如 Log 实现、MCP 实现等）。

使用 TypedDict 定义参数类型，获得类型安全和 IDE 支持。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Mapping
from .interfaces.chat_action import ChatActionParams
from .interfaces.attack_action import AttackActionParams


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
    def validate_params(self, params: Mapping[str, Any]) -> bool:
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
