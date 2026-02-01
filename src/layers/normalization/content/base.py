"""
结构化内容基类

定义StructuredContent抽象基类，支持方法多态。
"""

from abc import ABC, abstractmethod
from typing import Optional


class StructuredContent(ABC):
    """
    结构化内容基类（方法多态）

    所有类型化的内容（TextContent、GiftContent等）都继承此类。
    提供统一的方法接口，避免使用 isinstance。

    设计原则：
    - 使用方法多态替代 isinstance
    - 每个子类实现自己的逻辑
    - 提供便捷的查询方法
    """

    type: str

    @abstractmethod
    def get_importance(self) -> float:
        """
        获取重要性（0-1）

        Returns:
            float: 重要性值，范围0-1
        """
        pass

    @abstractmethod
    def get_display_text(self) -> str:
        """
        获取显示文本

        Returns:
            str: 用于显示的文本描述
        """
        pass

    def get_user_id(self) -> Optional[str]:
        """
        获取用户ID

        Returns:
            Optional[str]: 用户ID，如果没有则返回None
        """
        return None

    def requires_special_handling(self) -> bool:
        """
        是否需要特殊处理

        Returns:
            bool: 如果重要性>0.8则需要特殊处理
        """
        return self.get_importance() > 0.8

    def __repr__(self) -> str:
        """字符串表示"""
        return f"{self.__class__.__name__}(type={self.type}, importance={self.get_importance()})"
