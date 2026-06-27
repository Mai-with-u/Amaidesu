"""命令注册表模块"""

from typing import Dict, Optional

from src.modules.logging import get_logger


class CommandRegistry:
    """命令注册表。

    管理命令名称到动作字符串的映射。
    支持多个命令名称映射到同一个动作。
    """

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
        self._command_to_action: Dict[str, str] = {}

    def register_command(self, command_name: str, action: str) -> None:
        if not command_name:
            raise ValueError("命令名称不能为空")
        if not action:
            raise ValueError("动作不能为空")
        self._command_to_action[command_name.lower()] = action
        self.logger.debug(f"注册命令映射: '{command_name}' -> '{action}'")

    def get_action(self, command_name: str) -> Optional[str]:
        return self._command_to_action.get(command_name.lower())

    def is_supported_command(self, command_name: str) -> bool:
        return command_name.lower() in self._command_to_action

    def get_supported_commands(self) -> list[str]:
        return list(self._command_to_action.keys())

    def get_commands_for_action(self, action: str) -> list[str]:
        return [cmd for cmd, act in self._command_to_action.items() if act == action]

    def clear(self) -> None:
        self._command_to_action.clear()
        self.logger.debug("清空命令注册表")

    def load_from_config(self, command_mappings: Dict[str, str]) -> None:
        for command, action in command_mappings.items():
            self.register_command(command, action)
        self.logger.info(f"从配置加载了 {len(command_mappings)} 个命令映射")
