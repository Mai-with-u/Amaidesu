"""
抽象工厂模块

定义创建动作系列的抽象工厂接口。
每个具体工厂实现会创建一整套相关的动作对象（如 Log 系列、MCP 系列等）。
"""

from abc import ABC, abstractmethod
from ..actions.interfaces import IChatAction, IAttackAction


class AbstractActionFactory(ABC):
    """
    抽象动作工厂接口。

    定义创建各种动作的方法。
    具体工厂实现此接口，创建一整套特定实现的动作对象。
    """

    @abstractmethod
    def create_chat_action(self) -> IChatAction:
        """
        创建聊天动作实例。

        Returns:
            聊天动作实例
        """
        pass

    @abstractmethod
    def create_attack_action(self) -> IAttackAction:
        """
        创建攻击动作实例。

        Returns:
            攻击动作实例
        """
        pass

    @abstractmethod
    async def initialize(self) -> bool:
        """
        初始化工厂。
        具体工厂可以在此方法中进行必要的初始化操作。

        Returns:
            初始化是否成功
        """
        return True

    @abstractmethod
    async def cleanup(self):
        """
        清理工厂资源。
        具体工厂可以在此方法中进行资源清理。
        """
        pass

    @abstractmethod
    def get_factory_type(self) -> str:
        """
        获取工厂类型标识。

        Returns:
            工厂类型字符串（如 "log", "mcp"）
        """
        pass
