"""
动作系统模块

包含动作接口定义和各种具体实现。
"""

from .interfaces import IAction, IChatAction, IAttackAction
from .interfaces.chat_action import ChatActionParams
from .interfaces.attack_action import AttackActionParams

__all__ = [
    "IAction",
    "IChatAction",
    "IAttackAction",
    "ChatActionParams",
    "AttackActionParams",
]
