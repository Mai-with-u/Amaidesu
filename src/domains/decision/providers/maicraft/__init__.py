"""
Maicraft Decision Provider

基于抽象工厂模式的弹幕互动游戏决策 Provider。
支持通过配置切换不同的动作实现系列（如 Log、MCP 等）。
"""

from src.modules.registry import ProviderRegistry

from .action_registry import ActionRegistry
from .action_types import MaicraftActionType
from .command import Command
from .command_parser import CommandParser
from .config import MaicraftDecisionProviderConfig
from .factories import AbstractActionFactory, LogActionFactory, McpActionFactory
from .provider import MaicraftDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("maicraft", MaicraftDecisionProvider, source="builtin:maicraft")

__all__ = [
    "MaicraftDecisionProvider",
    "MaicraftDecisionProviderConfig",
    "MaicraftActionType",
    "AbstractActionFactory",
    "LogActionFactory",
    "McpActionFactory",
    "CommandParser",
    "ActionRegistry",
    "Command",
]
