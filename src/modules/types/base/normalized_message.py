"""
标准化消息类型

所有 Provider 产出的统一消息格式。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

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
    """

    text: str = Field(description="用于 LLM 处理的文本描述")
    source: str = Field(description="数据来源标识")
    data_type: str = Field(default="text", description="数据类型")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    timestamp: float = Field(default=0.0, description="消息时间戳")
    raw: Optional[BiliRawMessage] = Field(default=None, description="平台原始消息")

    @property
    def user_id(self) -> Optional[str]:
        """从 raw 中提取用户 ID"""
        if self.raw is None:
            return None
        return getattr(self.raw, "open_id", None)

    @property
    def user_name(self) -> Optional[str]:
        """从 raw 中提取用户名"""
        if self.raw is None:
            return None
        return getattr(self.raw, "uname", None)

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
