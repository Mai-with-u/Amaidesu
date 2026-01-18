"""
NormalizedMessage数据类型定义

Layer 2: Normalization的输出格式，表示标准化的消息。

核心改进：
- 保留原始结构化数据（不丢失信息）
- 提供文本描述（用于LLM处理）
- 自动计算重要性（0-1）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING
import time

if TYPE_CHECKING:
    from src.layers.normalization.content.base import StructuredContent
    from maim_message import MessageBase


@dataclass
class NormalizedMessage:
    """
    标准化消息（Layer 2: Normalization的输出）

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

    def to_message_base(self) -> Optional["MessageBase"]:
        """
        转换为MessageBase（仅用于MaiCoreDecisionProvider）

        将NormalizedMessage转换为MaiCore需要的MessageBase格式。

        Returns:
            MessageBase实例，如果转换失败返回None
        """
        try:
            from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo

            # 构建UserInfo
            user_id = self.user_id or "unknown"
            nickname = self.metadata.get("user_nickname", self.source)
            user_info = UserInfo(user_id=user_id, nickname=nickname)

            # 构建FormatInfo
            format_info = FormatInfo(font=None, color=None, size=None)

            # 构建Seg（文本片段）
            seg = Seg(type="text", data=self.text, format=format_info)

            # 构建MessageBase
            message = MessageBase(
                message_info=BaseMessageInfo(
                    message_id=f"normalized_{int(self.timestamp)}",
                    platform=self.source,
                    sender=user_info,
                    timestamp=self.timestamp,
                ),
                message_segment=seg,
            )

            return message
        except Exception as e:
            # 记录错误但不抛出异常，使用日志
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"转换为MessageBase失败: {e}", exc_info=True)
            return None

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
