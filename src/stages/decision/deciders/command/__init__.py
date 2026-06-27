"""Command Decision Decider

通用命令意图路由器：将命令形式的标准化消息转换为 Intent。
"""

from .command import Command
from .command_parser import CommandParser
from .command_registry import CommandRegistry
from .config import CommandDeciderConfig
from .command_decider import CommandDecider

__all__ = [
    "CommandDecider",
    "CommandDeciderConfig",
    "CommandParser",
    "CommandRegistry",
    "Command",
]
