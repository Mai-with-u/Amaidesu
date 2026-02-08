"""
NormalizedMessage数据类型定义

Input Domain中的Normalization模块输出格式，表示标准化的消息。

核心改进：
- 保留原始结构化数据（不丢失信息）
- 提供文本描述（用于LLM处理）
- 自动计算重要性（0-1）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING
import time

if TYPE_CHECKING:
    from src.domains.input.normalization.content.base import StructuredContent


@dataclass
class NormalizedMessage:
    """
    标准化消息（Input Domain中Normalization的输出）

    核心改进：
    - text: 用于LLM处理的文本描述
    - content: 保留原始结构化数据（不丢失信息）
    - importance: 预计算的重要性（0-1）

    Attributes:
        text: 文本描述（用于LLM处理）
        content: 结构化内容（保留原始数据）
        source: 数据来源（弹幕/控制台/等）
        data_type: 数据类型（text/gift/super_chat等）
        importance: 重要性（0-1，自动计算）
        metadata: 额外的元数据
        timestamp: 时间戳（Unix时间戳，秒）
    """

    text: str
    content: "StructuredContent"
    source: str
    data_type: str
    importance: float
    metadata: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}

        # 确保元数据包含基本字段
        if "type" not in self.metadata:
            self.metadata["type"] = self.data_type
        if "timestamp" not in self.metadata:
            self.metadata["timestamp"] = self.timestamp

    @property
    def user_id(self) -> Optional[str]:
        """获取用户ID（便捷方法）"""
        return self.content.get_user_id()

    @property
    def display_text(self) -> str:
        """获取显示文本（便捷方法）"""
        return self.content.get_display_text()

    def to_dict(self) -> dict:
        """
        转换为字典

        Returns:
            NormalizedMessage的字典表示
        """
        return {
            "text": self.text,
            "content": self.content,  # 注意：content需要单独序列化
            "source": self.source,
            "data_type": self.data_type,
            "importance": self.importance,
            "metadata": self.metadata.copy(),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_raw_data(
        cls,
        raw_data: Any,
        text: str,
        source: str,
        content: "StructuredContent",
        importance: float = 0.0,
    ) -> "NormalizedMessage":
        """
        从RawData创建NormalizedMessage

        Args:
            raw_data: 原始数据对象（RawData或dict）
            text: 标准化后的文本
            source: 数据源
            content: 结构化内容
            importance: 重要性

        Returns:
            NormalizedMessage对象
        """
        import time

        # 处理metadata（使用copy避免修改原始对象）
        if isinstance(raw_data, dict):
            metadata = raw_data.get("metadata", {}).copy()
        else:
            # 假设是RawData对象
            metadata = getattr(raw_data, "metadata", {}).copy()

        # 添加基本元数据
        metadata["source"] = source
        metadata["type"] = content.type
        metadata["timestamp"] = time.time()

        return cls(
            text=text,
            content=content,
            source=source,
            data_type=content.type,
            importance=importance,
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NormalizedMessage":
        """
        从字典创建NormalizedMessage（支持跨域反序列化）

        此方法用于从字典重建NormalizedMessage对象，特别适用于跨域事件传递的场景。
        支持自动重建TextContent、GiftContent、SuperChatContent等StructuredContent对象。

        Args:
            data: 字典数据，通常来自MessageReadyPayload的message字段

        Returns:
            NormalizedMessage对象

        Raises:
            ValueError: 如果data_type未知或缺少必要字段

        Example:
            >>> message_dict = {
            ...     "text": "hello",
            ...     "source": "console",
            ...     "data_type": "text",
            ...     "importance": 0.5,
            ...     "metadata": {},
            ...     "timestamp": 1234567890.0,
            ...     "content": {"type": "text", "text": "hello"}
            ... }
            >>> normalized = NormalizedMessage.from_dict(message_dict)
        """
        from src.domains.input.normalization.content import (
            TextContent,
            GiftContent,
            SuperChatContent,
        )

        # 提取基本字段
        text = data.get("text", "")
        source = data.get("source", "unknown")
        data_type = data.get("data_type", "text")
        importance = data.get("importance", 0.5)
        metadata = data.get("metadata", {})
        timestamp = data.get("timestamp", 0.0)

        # 重建content对象
        content_dict = data.get("content")
        if not content_dict or not isinstance(content_dict, dict):
            # 如果没有content信息，创建一个默认的TextContent
            content = TextContent(text=text)
        else:
            content_type = content_dict.get("type", data_type)

            # 根据type重建对应的StructuredContent
            if content_type == "text":
                content = TextContent(
                    text=content_dict.get("text", text),
                    user=content_dict.get("user"),
                    user_id=content_dict.get("user_id"),
                )
            elif content_type == "gift":
                content = GiftContent(
                    user=content_dict.get("user", ""),
                    user_id=content_dict.get("user_id", ""),
                    gift_name=content_dict.get("gift_name", ""),
                    gift_level=content_dict.get("gift_level", 1),
                    count=content_dict.get("count", 1),
                    value=content_dict.get("value", 0.0),
                )
            elif content_type == "super_chat":
                content = SuperChatContent(
                    user=content_dict.get("user", ""),
                    user_id=content_dict.get("user_id", ""),
                    amount=content_dict.get("amount", 0.0),
                    content=content_dict.get("content", text),
                )
            else:
                # 未知类型，使用TextContent作为fallback
                content = TextContent(text=text)

        # 创建NormalizedMessage对象
        return cls(
            text=text,
            content=content,
            source=source,
            data_type=data_type,
            importance=importance,
            metadata=metadata,
            timestamp=timestamp,
        )
