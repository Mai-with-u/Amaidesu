"""主播 Agent 命令子包

纯解析职责（不订阅事件、不调 LLM、不执行命令动作）。
命令解析是代码直连的内部件（不注册为工具）。
"""

from .command import Command
from .command_parser import CommandParser
from .command_registry import CommandRegistry

__all__ = ["Command", "CommandParser", "CommandRegistry"]
