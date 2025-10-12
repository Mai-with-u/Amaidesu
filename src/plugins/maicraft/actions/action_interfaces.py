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


class IChatAction(IAction):
    """
    聊天动作接口。

    参数类型：ChatActionParams
        - message: str - 要发送的聊天消息
    """

    @abstractmethod
    async def execute(self, params: ChatActionParams) -> bool:
        """
        执行聊天动作。

        Args:
            params: ChatActionParams 类型，包含 message 字段

        Returns:
            执行是否成功
        """
        pass

    def validate_params(self, params: Mapping[str, Any]) -> bool:
        """
        验证参数（默认实现）。
        子类可以重写以添加额外验证。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        # 检查必需字段
        return "message" in params and isinstance(params.get("message"), str)


class IAttackAction(IAction):
    """
    攻击动作接口。

    参数类型：AttackActionParams
        - mob_name: str - 要攻击的生物名称
    """

    @abstractmethod
    async def execute(self, params: AttackActionParams) -> bool:
        """
        执行攻击动作。

        Args:
            params: AttackActionParams 类型，包含 mob_name 字段

        Returns:
            执行是否成功
        """
        pass

    def validate_params(self, params: Mapping[str, Any]) -> bool:
        """
        验证参数（默认实现）。
        子类可以重写以添加额外验证。

        Args:
            params: 参数字典

        Returns:
            参数是否有效
        """
        # 检查必需字段
        return "mob_name" in params and isinstance(params.get("mob_name"), str)
