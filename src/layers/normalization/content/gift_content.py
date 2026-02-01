"""
礼物内容类型

表示礼物消息，自动计算重要性。
"""

from dataclasses import dataclass
from typing import Optional
from .base import StructuredContent


@dataclass
class GiftContent(StructuredContent):
    """
    礼物内容

    用于表示礼物/打赏消息。
    自动计算importance，基于礼物等级、价值、数量。

    Attributes:
        type: 内容类型（固定为"gift"）
        user: 送礼用户名
        user_id: 用户ID
        gift_name: 礼物名称
        gift_level: 礼物等级/稀有度（1-10）
        count: 礼物数量
        value: 礼物价值（元）
        importance: 重要性（自动计算）
    """

    type: str = "gift"
    user: str = ""
    user_id: str = ""
    gift_name: str = ""
    gift_level: int = 1
    count: int = 1
    value: float = 0.0
    importance: float = 0.0

    def __post_init__(self):
        """自动计算重要性"""
        # 基础重要性：基于等级
        base = min(self.gift_level / 10, 1.0)

        # 价值加成：最多+0.3
        value_boost = min(self.value / 10000, 0.3)

        # 数量加成：最多+0.2
        count_boost = min(self.count / 10, 0.2)

        # 计算最终重要性
        self.importance = min(base + value_boost + count_boost, 1.0)

    def get_importance(self) -> float:
        """
        获取重要性

        Returns:
            float: 重要性值（已自动计算）
        """
        return self.importance

    def get_display_text(self) -> str:
        """
        获取显示文本

        Returns:
            str: 礼物描述，例如"张三 送出了 1 个 火箭"
        """
        return f"{self.user} 送出了 {self.count} 个 {self.gift_name}"

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

        高价值礼物（importance > 0.7）需要特殊处理

        Returns:
            bool: 如果是高价值礼物则返回True
        """
        return self.importance > 0.7
