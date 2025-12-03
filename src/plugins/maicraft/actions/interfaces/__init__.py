"""动作接口模块"""

from .base import IAction
from .chat_action import IChatAction
from .attack_action import IAttackAction

__all__ = [
    "IAction",
    "IChatAction",
    "IAttackAction",
]
