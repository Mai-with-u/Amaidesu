"""
Input Domain 事件 Payload 定义

定义 Input Domain 相关的事件 Payload 类型。
- RawDataPayload: 原始数据事件
- MessageReadyPayload: 标准化消息就绪事件
"""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload

if TYPE_CHECKING:
    from src.modules.types.base.normalized_message import NormalizedMessage
    from src.modules.types.base.raw_data import RawData


class RawDataPayload(BasePayload):
    """
    原始数据事件 Payload

    事件名：CoreEvents.DATA_RAW
    发布者：InputProvider
    订阅者：InputDomain (Input Domain)

    表示 InputProvider 从外部采集到的原始数据。
    """

    content: Any = Field(..., description="原始数据内容（bytes, str, dict等）")
    source: str = Field(..., min_length=1, description="数据源标识符（如 'console_input', 'bili_danmaku'）")
    data_type: str = Field(..., description="数据类型（如 'text', 'gift', 'super_chat'）")
    timestamp: float = Field(default_factory=time.time, description="Unix时间戳（秒）")
    preserve_original: bool = Field(default=False, description="是否保留原始数据")
    original_data: Optional[Any] = Field(default=None, description="原始数据（如果content已被处理）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "用户输入的文本",
                "source": "console_input",
                "data_type": "text",
                "timestamp": 1706745600.0,
                "metadata": {"user_id": "12345", "username": "观众A"},
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：直接显示消息内容"""
        # 提取 content 中的关键信息
        if isinstance(self.content, dict):
            text = self.content.get("text", "")
            user_name = self.content.get("user_name", "")
        elif isinstance(self.message, dict):
            # NormalizedMessage 对象
            text = self.message.get("text", "")
            user_name = self.message.get("user_name", "")
        elif isinstance(self.message, str):
            text = self.message
            user_name = ""
        else:
            text = str(self.content)
            user_name = ""

        # 截断长文本
        if len(text) > 50:
            text = text[:47] + "..."

        # 构建格式：text (user_name)
        if user_name:
            return f'{text} ({user_name})'
        return text

    def _debug_fields(self) -> List[str]:
        """返回需要显示的字段"""
        return ["source", "data_type", "content"]

    @classmethod
    def from_raw_data(cls, raw_data: "RawData") -> "RawDataPayload":
        """
        从 RawData 对象创建 Payload

        Args:
            raw_data: RawData 对象

        Returns:
            RawDataPayload 实例
        """
        return cls(
            content=raw_data.content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            timestamp=raw_data.timestamp,
            preserve_original=raw_data.preserve_original,
            original_data=raw_data.original_data,
            metadata=raw_data.metadata.copy(),
        )


class MessageReadyPayload(BasePayload):
    """
    标准化消息就绪事件 Payload

    事件名：CoreEvents.DATA_MESSAGE
    发布者：InputDomain
    订阅者：DecisionManager (Decision Domain)

    表示 InputDomain 完成了数据标准化处理，生成了 NormalizedMessage。

    注意：message 字段使用 Dict[str, Any] 而不是 NormalizedMessage 对象，
    因为 NormalizedMessage.content 包含不可序列化的 StructuredContent 对象。
    """

    # 使用 Dict 格式，因为 NormalizedMessage.content 包含不可序列化的对象
    message: Dict[str, Any] = Field(..., description="标准化消息（NormalizedMessage 序列化后的字典）")
    source: str = Field(..., min_length=1, description="数据源")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": {
                    "text": "你好，今天天气怎么样？",
                    "content": {"type": "TextContent", "text": "你好，今天天气怎么样？"},
                    "source": "bili_danmaku",
                    "data_type": "text",
                    "importance": 0.5,
                    "metadata": {"user_id": "12345", "username": "观众A"},
                    "timestamp": 1706745600.0,
                },
                "source": "bili_danmaku",
                "timestamp": 1706745600.0,
                "metadata": {"room_id": "123456"},
            }
        }
    )

    def __str__(self) -> str:
        """简化格式：直接显示消息内容"""
        # 提取 text 和 user_name
        if isinstance(self.message, dict):
            text = self.message.get("text", "")
            user_name = self.message.get("user_name", "")
        elif isinstance(self.message, str):
            text = self.message
            user_name = ""
        else:
            text = str(self.message)
            user_name = ""

        # 截断长文本
        if len(text) > 50:
            text = text[:47] + "..."

        # 返回格式：text (user_name)
        if user_name:
            return f'{text} ({user_name})'
        return text

    def _debug_fields(self) -> List[str]:
        """返回需要显示的字段"""
        # 只显示 message 字段（因为 __str__ 已自定义）
        return ["message"]

    def _format_field_value(self, value: Any, indent: int = 0) -> str:
        """格式化字段值，对 message 字段进行特殊处理（单行格式）"""
        # 对于 message 字段（NormalizedMessage 对象或 Dict），直接返回文本内容
        if hasattr(value, "text"):
            # NormalizedMessage 对象 - 直接返回文本和用户名
            text = value.text if hasattr(value, "text") else ""
            user_name = value.user_name if hasattr(value, "user_name") else ""
            if user_name:
                return f'{text} ({user_name})'
            return text
        elif isinstance(value, dict) and "text" in value:
            # 字典格式（向后兼容）
            text = value.get("text", "")
            user_name = value.get("user_name", "")
            if user_name:
                return f'{text} ({user_name})'
            return text
        # 其他字段使用基类默认格式化
        return super()._format_field_value(value, indent)

    @classmethod
    def from_normalized_message(
        cls, normalized_message: "NormalizedMessage", **extra_metadata
    ) -> "MessageReadyPayload":
        """
        从 NormalizedMessage 对象创建 Payload

        Args:
            normalized_message: NormalizedMessage 对象
            **extra_metadata: 额外的元数据

        Returns:
            MessageReadyPayload 实例
        """
        # 使用 model_dump() 序列化 NormalizedMessage (Python 模式)
        # mode='python' 保留 Python 对象，mode='json' 转换为 JSON 兼容格式
        metadata = normalized_message.metadata.copy()
        metadata.update(extra_metadata)

        return cls(
            message=normalized_message.model_dump(mode="python"),
            source=normalized_message.source,
            timestamp=normalized_message.timestamp,
            metadata=metadata,
        )
