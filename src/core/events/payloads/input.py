"""
Input Domain 事件 Payload 定义

定义 Input Domain 相关的事件 Payload 类型。
- RawDataPayload: 原始数据事件
- MessageReadyPayload: 标准化消息就绪事件
"""

from typing import Any, Dict, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict
import time

if TYPE_CHECKING:
    from src.core.base.normalized_message import NormalizedMessage
    from src.core.base.raw_data import RawData


class RawDataPayload(BaseModel):
    """
    原始数据事件 Payload

    事件名：CoreEvents.PERCEPTION_RAW_DATA_GENERATED
    发布者：InputProvider
    订阅者：InputLayer（Layer 1-2）

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


class MessageReadyPayload(BaseModel):
    """
    标准化消息就绪事件 Payload

    事件名：CoreEvents.NORMALIZATION_MESSAGE_READY
    发布者：InputLayer
    订阅者：DecisionManager（Layer 3）

    表示 InputLayer 完成了数据标准化处理，生成了 NormalizedMessage。
    """

    message: Dict[str, Any] = Field(..., description="标准化消息（NormalizedMessage 序列化）")
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

    @classmethod
    def from_normalized_message(cls, normalized_message: "NormalizedMessage", **extra_metadata) -> "MessageReadyPayload":
        """
        从 NormalizedMessage 对象创建 Payload

        Args:
            normalized_message: NormalizedMessage 对象
            **extra_metadata: 额外的元数据

        Returns:
            MessageReadyPayload 实例
        """
        # 将 NormalizedMessage 转换为字典
        metadata = normalized_message.metadata.copy()
        metadata.update(extra_metadata)

        return cls(
            message=normalized_message.to_dict(),
            source=normalized_message.source,
            timestamp=normalized_message.timestamp,
            metadata=metadata,
        )
