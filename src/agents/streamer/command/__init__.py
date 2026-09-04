"""主播 Agent 命令子包（Wave 6 纯解析保留）

Wave 6 迁移：原 ``stages/decision/deciders/command/`` 子包整体移植到
``src/agents/streamer/command/``；保持纯解析职责（不订阅事件、不调 LLM、
不发布 Intent）。CommandDecider 已 REWRITE 为 Tool（见
``agents/streamer/tools/command_tool.py``）。
"""

from .command import Command
from .command_parser import CommandParser
from .command_registry import CommandRegistry

__all__ = ["Command", "CommandParser", "CommandRegistry"]
