"""
动作类型定义模块

定义所有可用的动作类型枚举。
"""

from enum import Enum, unique


@unique
class ActionType(Enum):
    """
    动作类型枚举。

    每个枚举值代表一种抽象的动作类型。
    具体的动作实现由工厂决定。
    """

    CHAT = "chat"
    """聊天动作"""

    ATTACK = "attack"
    """攻击动作"""

    @classmethod
    def from_string(cls, action_type: str) -> "ActionType":
        """
        从字符串创建 ActionType 枚举。

        Args:
            action_type: 动作类型字符串

        Returns:
            对应的 ActionType 枚举

        Raises:
            ValueError: 如果动作类型不存在
        """
        try:
            return cls(action_type.lower())
        except ValueError:
            raise ValueError(f"未知的动作类型: {action_type}")

    @classmethod
    def all_types(cls) -> list[str]:
        """
        获取所有动作类型的字符串列表。

        Returns:
            所有动作类型的字符串列表
        """
        return [action_type.value for action_type in cls]
