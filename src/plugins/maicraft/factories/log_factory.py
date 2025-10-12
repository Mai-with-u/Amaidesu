"""
Log 动作工厂

创建 Log 系列的动作实现。
这些动作仅将内容输出到日志，主要用于测试和调试。
"""

from src.utils.logger import get_logger
from .abstract_factory import AbstractActionFactory
from ..actions.action_interfaces import IChatAction, IAttackAction
from ..actions.implementations.log_actions import LogChatAction, LogAttackAction


class LogActionFactory(AbstractActionFactory):
    """
    Log 动作工厂实现。

    创建一整套 Log 版本的动作对象。
    """

    def __init__(self):
        self.logger = get_logger("LogActionFactory")
        self.initialized = False

    def create_chat_action(self) -> IChatAction:
        """
        创建 Log 版本的聊天动作。

        Returns:
            LogChatAction 实例
        """
        return LogChatAction()

    def create_attack_action(self) -> IAttackAction:
        """
        创建 Log 版本的攻击动作。

        Returns:
            LogAttackAction 实例
        """
        return LogAttackAction()

    async def initialize(self) -> bool:
        """
        初始化 Log 工厂。

        Returns:
            初始化是否成功
        """
        try:
            self.logger.info("Log 动作工厂初始化完成")
            self.initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Log 工厂初始化失败: {e}", exc_info=True)
            return False

    async def cleanup(self):
        """清理 Log 工厂资源"""
        self.logger.info("Log 动作工厂清理完成")
        self.initialized = False

    def get_factory_type(self) -> str:
        """
        获取工厂类型。

        Returns:
            "log"
        """
        return "log"
