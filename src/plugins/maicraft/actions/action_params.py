"""
动作参数定义模块

使用 TypedDict 定义每个动作的参数类型。
这样可以获得类型检查和 IDE 代码补全支持，同时保持接口的统一性。
"""

from typing import TypedDict


class ChatActionParams(TypedDict):
    """
    聊天动作参数。

    Attributes:
        message: 要发送的聊天消息
    """

    message: str


class AttackActionParams(TypedDict):
    """
    攻击动作参数。

    Attributes:
        mob_name: 要攻击的生物名称
    """

    mob_name: str


# 未来可以继续添加其他动作的参数类型
# class MoveActionParams(TypedDict):
#     x: float
#     y: float
#     z: float
