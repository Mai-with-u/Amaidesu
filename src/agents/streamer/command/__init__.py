"""主播 Agent 命令子包

纯解析职责（不订阅事件、不调 LLM、不执行命令动作）。
命令的 LLM 调用入口见 ``agents/streamer/tools/command_tool.py``。
"""

from .command import Command
from .command_parser import CommandParser
from .command_registry import CommandRegistry

__all__ = ["Command", "CommandParser", "CommandRegistry"]
