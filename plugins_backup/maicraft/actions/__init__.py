"""
动作系统模块

包含动作接口定义和各种具体实现。
"""

from .interfaces import IAction, IAttackAction, IChatAction
from .interfaces.attack_action import AttackActionParams
from .interfaces.chat_action import ChatActionParams

__all__ = [
    "IAction",
    "IChatAction",
    "IAttackAction",
    "ChatActionParams",
    "AttackActionParams",
]
