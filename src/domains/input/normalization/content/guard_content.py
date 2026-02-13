"""
大航海内容类型
"""

from typing import Literal, Optional

from pydantic import Field

from .base import StructuredContent


class GuardContent(StructuredContent):
    """大航海内容类型"""

    type: Literal["guard"] = "guard"
    user: str = Field(default="", description="用户名")
    user_id: str = Field(default="", description="用户ID")
    level: Literal["舰长", "提督", "总督"] = Field(default="舰长", description="大航海等级")

    def get_importance(self) -> float:
        """获取重要性（大航海都是高价值）"""
        level_scores = {"总督": 1.0, "提督": 0.9, "舰长": 0.8}
        return level_scores.get(self.level, 0.8)

    def get_display_text(self) -> str:
        """获取显示文本"""
        return f"{self.user} 开通了 {self.level}"

    def get_user_id(self) -> Optional[str]:
        """获取用户ID"""
        return self.user_id
