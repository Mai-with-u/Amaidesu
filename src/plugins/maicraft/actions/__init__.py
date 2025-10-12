"""
动作系统模块

包含动作接口定义和各种具体实现。
"""

from .interfaces import IAction, IChatAction, IAttackAction
from .action_params import ChatActionParams, AttackActionParams

__all__ = [
    "IAction",
    "IChatAction",
    "IAttackAction",
    "ChatActionParams",
    "AttackActionParams",
]
