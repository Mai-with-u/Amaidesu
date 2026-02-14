"""
标准化消息类型

所有 Provider 产出的统一消息格式。
"""

import time
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

# Forward reference for BiliRawMessage - 避免循环导入
BiliRawMessage = Any  # 实际类型在运行时动态绑定


class NormalizedMessage(BaseModel):
    """
    标准化消息

    所有 Provider 产出的统一消息格式，包含文本描述、数据类型、重要性和原始消息。

    Attributes:
        text: 用于 LLM 处理的文本描述
        source: 数据来源标识（如 "bili_danmaku_official", "console"）
        data_type: 数据类型（text, gift, super_chat, guard, enter）
        importance: 重要性评分（0-1），用于过滤和优先级排序
        timestamp: 消息时间戳
        raw: 平台原始消息对象（类型安全，可选）
        metadata: 扩展元数据字典
    """

    text: str = Field(description="用于 LLM 处理的文本描述")
    source: str = Field(description="数据来源标识")
    data_type: str = Field(default="text", description="数据类型")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    timestamp: float = Field(default=0.0, description="消息时间戳")
    raw: Optional[BiliRawMessage] = Field(default=None, description="平台原始消息")
    metadata: dict = Field(default_factory=dict, description="扩展元数据")

    @model_validator(mode="before")
    @classmethod
    def auto_set_timestamp_and_metadata(cls, values):
        """自动设置 timestamp 和 metadata"""
        if isinstance(values, dict):
            # 自动设置 timestamp
            if values.get("timestamp", 0.0) == 0.0:
                values["timestamp"] = time.time()
            # 自动设置 metadata
            if "metadata" not in values:
                values["metadata"] = {}
            if "type" not in values["metadata"]:
                values["metadata"]["type"] = values.get("data_type", "text")
            if "timestamp" not in values["metadata"]:
                values["metadata"]["timestamp"] = values.get("timestamp")
        return values

    @property
    def user_id(self) -> Optional[str]:
        """从 raw 中提取用户 ID"""
        if self.raw is None:
            return None
        # 优先尝试 open_id 属性
        open_id = getattr(self.raw, "open_id", None)
        if open_id is not None:
            return open_id
        # 如果没有 open_id，尝试调用 get_user_id() 方法
        get_user_id = getattr(self.raw, "get_user_id", None)
        if get_user_id is not None:
            try:
                return get_user_id()
            except Exception:
                pass
        return None

    @property
    def user_name(self) -> Optional[str]:
        """从 raw 中提取用户名"""
        if self.raw is None:
            return None
        return getattr(self.raw, "uname", None)

    @property
    def display_text(self) -> str:
        """获取显示文本，优先从 raw 对象获取，否则使用 text"""
        if self.raw is None:
            return self.text
        return getattr(self.raw, "get_display_text", lambda: self.text)()

    @classmethod
    def from_raw_data(
        cls,
        raw_data: dict,
        text: str,
        source: str,
        raw: Any = None,
        data_type: str = "text",
        importance: float = 0.5,
    ) -> "NormalizedMessage":
        """
        从原始数据创建 NormalizedMessage

        Args:
            raw_data: 原始数据字典
            text: 标准化后的文本
            source: 数据来源
            raw: 原始消息对象
            data_type: 数据类型
            importance: 重要性评分

        Returns:
            NormalizedMessage 实例
        """
        metadata = raw_data.get("metadata", {}).copy() if isinstance(raw_data.get("metadata"), dict) else {}
        metadata["source"] = source

        return cls(
            text=text,
            source=source,
            raw=raw,
            data_type=data_type,
            importance=importance,
            metadata=metadata,
        )

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        result = {
            "text": self.text,
            "source": self.source,
            "data_type": self.data_type,
            "importance": self.importance,
            "timestamp": self.timestamp,
        }
        if self.raw is not None:
            result["raw_data"] = self.raw.model_dump()
        return result
