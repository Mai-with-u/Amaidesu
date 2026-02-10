"""动作接口模块"""

from .attack_action import IAttackAction
from .base import IAction
from .chat_action import IChatAction

__all__ = [
    "IAction",
    "IChatAction",
    "IAttackAction",
]
