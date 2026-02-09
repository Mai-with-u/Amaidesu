"""
文本内容类型

表示普通文本消息。
"""

from typing import Literal, Optional
from pydantic import Field
from .base import StructuredContent


class TextContent(StructuredContent):
    """
    文本内容

    用于表示普通的文本消息。

    Attributes:
        type: 内容类型（固定为"text"）
        text: 文本内容
        user: 用户名（可选）
        user_id: 用户ID（可选）
    """

    type: Literal["text"] = "text"
    text: str = Field(default="")
    user: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)

    def get_importance(self) -> float:
        """
        获取重要性

        文本消息的基础重要性为0.3
        """
        return 0.3

    def get_display_text(self) -> str:
        """
        获取显示文本

        Returns:
            str: 文本内容本身
        """
        return self.text

    def get_user_id(self) -> Optional[str]:
        """
        获取用户ID

        Returns:
            Optional[str]: 用户ID，如果有
        """
        return self.user_id
