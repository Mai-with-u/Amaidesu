"""Log 系列动作实现"""

from .attack_action import LogAttackAction
from .chat_action import LogChatAction

__all__ = [
    "LogChatAction",
    "LogAttackAction",
]
