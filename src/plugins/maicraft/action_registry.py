from typing import Dict, Type, Optional
from .actions.base_action import BaseAction
from src.utils.logger import get_logger


class ActionRegistry:
    """
    行动注册表，管理命令名称到行动类的映射关系。
    支持命令名称和行动标识的解耦。
    """

    def __init__(self):
        self.logger = get_logger("ActionRegistry")
        # 命令名称到行动类的映射
        self._command_to_action: Dict[str, Type[BaseAction]] = {}
        # 行动类的实例缓存（可选，用于性能优化）
        self._action_instances: Dict[Type[BaseAction], BaseAction] = {}

    def register_action(self, command_name: str, action_class: Type[BaseAction]):
        """
        注册行动类到命令名称。

        Args:
            command_name: 命令名称
            action_class: 行动类
        """
        if not issubclass(action_class, BaseAction):
            raise ValueError(f"行动类 {action_class} 必须继承自 BaseAction")

        self._command_to_action[command_name.lower()] = action_class
        self.logger.debug(f"注册行动: 命令 '{command_name}' -> {action_class.__name__}")

    def get_action_class(self, command_name: str) -> Optional[Type[BaseAction]]:
        """
        根据命令名称获取对应的行动类。

        Args:
            command_name: 命令名称

        Returns:
            对应的行动类，如果未找到则返回None
        """
        return self._command_to_action.get(command_name.lower())

    def create_action(self, command_name: str) -> Optional[BaseAction]:
        """
        根据命令名称创建行动实例。

        Args:
            command_name: 命令名称

        Returns:
            行动实例，如果未找到对应的行动类则返回None
        """
        action_class = self.get_action_class(command_name)
        if action_class is None:
            return None

        # 检查是否已有缓存的实例
        if action_class in self._action_instances:
            return self._action_instances[action_class]

        # 创建新实例
        try:
            action_instance = action_class()
            self._action_instances[action_class] = action_instance
            self.logger.debug(f"创建行动实例: {action_class.__name__}")
            return action_instance
        except Exception as e:
            self.logger.error(f"创建行动实例失败: {action_class.__name__}, 错误: {e}")
            return None

    def is_supported_command(self, command_name: str) -> bool:
        """
        检查是否支持指定的命令。

        Args:
            command_name: 命令名称

        Returns:
            是否支持该命令
        """
        return command_name.lower() in self._command_to_action

    def get_supported_commands(self) -> list[str]:
        """
        获取所有支持的命令列表。

        Returns:
            支持的命令名称列表
        """
        return list(self._command_to_action.keys())

    def clear(self):
        """清空所有注册的行动和缓存的实例"""
        self._command_to_action.clear()
        self._action_instances.clear()
        self.logger.debug("清空行动注册表")
