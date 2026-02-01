"""
醒目留言内容类型

表示醒目留言（Super Chat）消息。
"""

from dataclasses import dataclass
from typing import Optional
from .base import StructuredContent


@dataclass
class SuperChatContent(StructuredContent):
    """
    醒目留言内容

    用于表示醒目留言（付费留言）消息。
    重要性基于金额自动计算。

    Attributes:
        type: 内容类型（固定为"super_chat"）
        user: 用户名
        user_id: 用户ID
        amount: 金额（元）
        content: 留言内容
    """

    type: str = "super_chat"
    user: str = ""
    user_id: str = ""
    amount: float = 0.0
    content: str = ""

    def get_importance(self) -> float:
        """
        获取重要性

        重要性基于金额：100元 = 1.0

        Returns:
            float: 重要性值
        """
        return min(self.amount / 100, 1.0)

    def get_display_text(self) -> str:
        """
        获取显示文本

        Returns:
            str: 醒目留言描述，例如"醒目留言: 谢谢大家的支持！"
        """
        return f"醒目留言: {self.content}"

    def get_user_id(self) -> Optional[str]:
        """
        获取用户ID

        Returns:
            Optional[str]: 用户ID
        """
        return self.user_id

    def requires_special_handling(self) -> bool:
        """
        是否需要特殊处理

        高金额醒目留言（>50元）需要特殊处理

        Returns:
            bool: 如果金额>50则返回True
        """
        return self.amount > 50
