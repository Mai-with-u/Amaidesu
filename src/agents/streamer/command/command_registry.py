"""命令注册表模块：命令名 → 动作。

两类动作并存，合起来就是白名单：
- 委派动作（字符串）：给委派型游戏的自然语言指令，路由经 ``framework_delegate`` 送出
- 工具动作（工具全名 + 固定参数）：给遥控型游戏的一次工具调用，路由经工具注册表直接调用
同名命令以工具动作为准（配置里两表都写了同一个名，按更具体的那张算），加载时记告警。
"""

from typing import Any, Dict, Iterable, Optional

from src.modules.logging import get_logger


class CommandRegistry:
    """命令注册表。

    管理命令名称到动作的映射；支持多个命令名称映射到同一个动作。
    """

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._command_to_action: Dict[str, str] = {}
        # 命令名 → (工具全名, 固定参数)
        self._command_to_tool: Dict[str, tuple[str, Dict[str, Any]]] = {}

    def register_command(self, command_name: str, action: str) -> None:
        if not command_name:
            raise ValueError("命令名称不能为空")
        if not action:
            raise ValueError("动作不能为空")
        self._command_to_action[command_name.lower()] = action
        self.logger.debug(f"注册命令映射: '{command_name}' -> '{action}'")

    def register_tool_command(self, command_name: str, tool: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        """登记一条工具动作：命令命中后直接调用 ``tool``，参数写死为 ``arguments``。"""
        if not command_name:
            raise ValueError("命令名称不能为空")
        if not tool:
            raise ValueError("工具全名不能为空")
        key = command_name.lower()
        if key in self._command_to_action:
            self.logger.warning(f"命令 '{command_name}' 同时配了委派动作与工具动作，以工具动作为准")
        self._command_to_tool[key] = (tool, dict(arguments or {}))
        self.logger.debug(f"注册命令工具映射: '{command_name}' -> {tool}({arguments or {}})")

    def get_action(self, command_name: str) -> Optional[str]:
        return self._command_to_action.get(command_name.lower())

    def get_tool_action(self, command_name: str) -> Optional[tuple[str, Dict[str, Any]]]:
        """取工具动作 ``(工具全名, 参数副本)``；没配返回 None。"""
        found = self._command_to_tool.get(command_name.lower())
        if found is None:
            return None
        tool, arguments = found
        return tool, dict(arguments)

    def is_supported_command(self, command_name: str) -> bool:
        key = command_name.lower()
        return key in self._command_to_action or key in self._command_to_tool

    def get_supported_commands(self) -> list[str]:
        return sorted(set(self._command_to_action) | set(self._command_to_tool))

    def get_commands_for_action(self, action: str) -> list[str]:
        return [cmd for cmd, act in self._command_to_action.items() if act == action]

    def clear(self) -> None:
        self._command_to_action.clear()
        self._command_to_tool.clear()
        self.logger.debug("清空命令注册表")

    def load_from_config(self, command_mappings: Dict[str, str]) -> None:
        for command, action in command_mappings.items():
            self.register_command(command, action)
        self.logger.info(f"从配置加载了 {len(command_mappings)} 个命令映射")

    def load_tool_mappings(self, items: Iterable[Any]) -> None:
        """从配置加载工具动作（元素带 ``command`` / ``tool`` / ``arguments`` 属性）。

        命令名或工具名留空的条目是没填完的配置：记告警跳过，不让半条映射把
        整张表拖垮。
        """
        loaded = 0
        for item in items:
            command = str(getattr(item, "command", "") or "")
            tool = str(getattr(item, "tool", "") or "")
            arguments = getattr(item, "arguments", None)
            if not command or not tool:
                self.logger.warning(f"命令工具映射缺少命令名或工具名，跳过: command={command!r} tool={tool!r}")
                continue
            self.register_tool_command(command, tool, arguments if isinstance(arguments, dict) else {})
            loaded += 1
        self.logger.info(f"从配置加载了 {loaded} 个命令工具映射")
