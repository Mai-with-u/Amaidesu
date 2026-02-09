"""
动作注册表模块

管理命令名称到动作类型的映射关系。
支持多个命令名称映射到同一个动作类型。
"""

from typing import Dict, Optional

from src.core.utils.logger import get_logger

from .action_types import MaicraftActionType


class ActionRegistry:
    """
    动作注册表。

    管理命令名称到动作类型的映射。
    命令映射到抽象的动作类型，具体实现由工厂决定。

    示例:
        "chat" 命令 -> MaicraftActionType.CHAT
        "say" 命令 -> MaicraftActionType.CHAT
        "attack" 命令 -> MaicraftActionType.ATTACK
    """

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
        # 命令名称到动作类型的映射
        self._command_to_action_type: Dict[str, MaicraftActionType] = {}

    def register_command(self, command_name: str, action_type: MaicraftActionType) -> None:
        """
        注册命令到动作类型的映射。

        Args:
            command_name: 命令名称
            action_type: 动作类型枚举

        Raises:
            ValueError: 如果参数无效
        """
        if not command_name:
            raise ValueError("命令名称不能为空")

        if not isinstance(action_type, MaicraftActionType):
            raise ValueError(f"action_type 必须是 MaicraftActionType 枚举: {type(action_type)}")

        command_name_lower = command_name.lower()
        self._command_to_action_type[command_name_lower] = action_type
        self.logger.debug(f"注册命令映射: '{command_name}' -> {action_type.value}")

    def get_action_type(self, command_name: str) -> Optional[MaicraftActionType]:
        """
        根据命令名称获取对应的动作类型。

        Args:
            command_name: 命令名称

        Returns:
            对应的动作类型，如果未找到则返回 None
        """
        return self._command_to_action_type.get(command_name.lower())

    def is_supported_command(self, command_name: str) -> bool:
        """
        检查是否支持指定的命令。

        Args:
            command_name: 命令名称

        Returns:
            是否支持该命令
        """
        return command_name.lower() in self._command_to_action_type

    def get_supported_commands(self) -> list[str]:
        """
        获取所有支持的命令列表。

        Returns:
            支持的命令名称列表
        """
        return list(self._command_to_action_type.keys())

    def get_commands_for_action_type(self, action_type: MaicraftActionType) -> list[str]:
        """
        获取映射到指定动作类型的所有命令。

        Args:
            action_type: 动作类型

        Returns:
            映射到该动作类型的命令名称列表
        """
        return [cmd for cmd, atype in self._command_to_action_type.items() if atype == action_type]

    def clear(self) -> None:
        """清空所有注册的命令映射"""
        self._command_to_action_type.clear()
        self.logger.debug("清空动作注册表")

    def load_from_config(self, command_mappings: Dict[str, str]) -> None:
        """
        从配置字典加载命令映射。

        Args:
            command_mappings: 命令名称到动作类型字符串的映射
                例如: {"chat": "chat", "say": "chat", "attack": "attack"}

        Raises:
            ValueError: 如果动作类型字符串无效
        """
        for command, action_type_str in command_mappings.items():
            try:
                action_type = MaicraftActionType.from_string(action_type_str)
                self.register_command(command, action_type)
            except ValueError as e:
                self.logger.error(f"加载命令映射失败: {command} -> {action_type_str}, 错误: {e}")
                raise

        self.logger.info(f"从配置加载了 {len(command_mappings)} 个命令映射")
