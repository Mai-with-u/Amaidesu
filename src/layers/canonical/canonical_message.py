"""
CanonicalMessage - Layer 3: 中间表示

统一的中间表示格式,支持元数据传递。
"""

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.data_types.normalized_text import NormalizedText
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from maim_message import MessageBase


@dataclass
class CanonicalMessage:
    """
    规范化消息(Layer 3: 中间表示)

    核心职责:
    - 统一的中间表示格式
    - 支持元数据传递
    - 可序列化/反序列化
    - 兼容MessageBase(向后兼容)

    Attributes:
        text: 文本内容
        source: 数据来源(弹幕/控制台/等)
        metadata: 元数据(用户ID、时间戳等)
        original_message: 原始MessageBase(保留兼容)
        timestamp: 时间戳(Unix时间戳,秒)
    """

    text: str
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    # data_ref: Optional[str] = None  # DataCache引用已移除(未实际使用)
    original_message: Optional["MessageBase"] = None
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """初始化后处理，确保metadata被复制"""
        if self.metadata is None:
            self.metadata = {}
        else:
            # 深度复制metadata，避免外部修改
            self.metadata = self.metadata.copy()

    def to_dict(self) -> dict:
        """
        序列化为字典

        Returns:
            序列化后的字典
        """
        # 转换为字典,但跳过original_message(不可序列化)
        data = asdict(self)
        data.pop("original_message", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CanonicalMessage":
        """
        从字典反序列化

        Args:
            data: 字典数据

        Returns:
            CanonicalMessage实例
        """
        # 只提取CanonicalMessage的字段，忽略额外字段
        valid_fields = {
            "text": data.get("text", ""),
            "source": data.get("source", "unknown"),
            "metadata": data.get("metadata", {}).copy(),
            "data_ref": data.get("data_ref"),
            "original_message": None,  # 始终设为None，因为它无法序列化
            "timestamp": data.get("timestamp", time.time()),
        }
        return cls(**valid_fields)

    @classmethod
    def from_normalized_text(cls, normalized_text: NormalizedText) -> "CanonicalMessage":
        """
        从NormalizedText创建CanonicalMessage

        Args:
            normalized_text: 标准化文本

        Returns:
            CanonicalMessage实例
        """
        # 提取metadata
        metadata = normalized_text.metadata.copy()

        # 保留source和type信息
        if "source" not in metadata:
            metadata["source"] = normalized_text.source
        if "type" not in metadata:
            metadata["type"] = normalized_text.type

        return cls(
            text=normalized_text.text,
            source=normalized_text.source,
            metadata=metadata,
            data_ref=normalized_text.data_ref,
            timestamp=time.time(),
        )

    def to_message_base(self) -> Optional["MessageBase"]:
        """
        转换为MessageBase(发送到旧系统)

        Returns:
            MessageBase实例,如果无法转换则返回None

        注意:
            这是向后兼容的方法,用于与旧系统交互。
            新代码应直接使用CanonicalMessage。
        """
        try:
            from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo

            # 构建UserInfo
            user_info = UserInfo(
                user_id=self.metadata.get("user_id", "unknown"),
                nickname=self.metadata.get("user_nickname", self.source),
            )

            # 构建FormatInfo
            format_info = FormatInfo(
                font=None,
                color=None,
                size=None,
            )

            # 构建Seg(文本片段)
            seg = Seg(
                type="text",
                data=self.text,
                format=format_info,
            )

            # 构建MessageBase
            message = MessageBase(
                message_info=BaseMessageInfo(
                    message_id=f"canonical_{int(self.timestamp)}",
                    platform=self.source,
                    sender=user_info,
                    timestamp=self.timestamp,
                ),
                message_segment=seg,
            )

            return message
        except Exception as e:
            logger = get_logger("CanonicalMessage")
            logger.error(f"转换为MessageBase失败: {e}", exc_info=True)
            return None

    def __repr__(self) -> str:
        """字符串表示"""
        return f"CanonicalMessage(text='{self.text[:50]}...', source='{self.source}', timestamp={self.timestamp})"


class MessageBuilder:
    """
    消息构建器工具

    提供CanonicalMessage和MessageBase之间的转换功能。
    """

    logger = get_logger("MessageBuilder")

    @staticmethod
    def build_from_normalized_text(normalized_text: NormalizedText) -> CanonicalMessage:
        """
        从NormalizedText构建CanonicalMessage

        Args:
            normalized_text: 标准化文本

        Returns:
            CanonicalMessage实例
        """
        return CanonicalMessage.from_normalized_text(normalized_text)

    @staticmethod
    def build_from_text(text: str, source: str, **metadata) -> CanonicalMessage:
        """
        从文本构建CanonicalMessage

        Args:
            text: 文本内容
            source: 数据来源
            **metadata: 其他元数据

        Returns:
            CanonicalMessage实例
        """
        return CanonicalMessage(
            text=text,
            source=source,
            metadata=metadata,
            timestamp=time.time(),
        )

    @staticmethod
    def build_from_message_base(message: "MessageBase") -> CanonicalMessage:
        """
        从MessageBase构建CanonicalMessage

        Args:
            message: MessageBase实例

        Returns:
            CanonicalMessage实例
        """
        # 提取文本
        text = ""
        if message.message_segment and hasattr(message.message_segment, "data"):
            if isinstance(message.message_segment.data, str):
                text = message.message_segment.data
            elif isinstance(message.message_segment.data, list):
                # 处理seglist
                for seg in message.message_segment.data:
                    if hasattr(seg, "data") and isinstance(seg.data, str):
                        text += seg.data

        # 提取source
        source = "unknown"
        if message.message_info:
            source = message.message_info.platform or "unknown"

        # 提取metadata
        metadata = {}
        if message.message_info:
            if message.message_info.sender:
                metadata["user_id"] = message.message_info.sender.user_id
                metadata["user_nickname"] = message.message_info.sender.nickname
            metadata["message_id"] = message.message_info.message_id
            metadata["timestamp"] = message.message_info.timestamp

        return CanonicalMessage(
            text=text,
            source=source,
            metadata=metadata,
            original_message=message,
            timestamp=time.time(),
        )
