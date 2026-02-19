"""
标准化消息类型

所有 Provider 产出的统一消息格式。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

logger = get_logger(__name__)

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
        user_id: 用户 ID（可选）
        user_nickname: 用户昵称（可选）
        platform: 平台来源（可选）
        room_id: 直播间 ID（可选）
    """

    text: str = Field(description="用于 LLM 处理的文本描述")
    source: str = Field(description="数据来源标识")
    data_type: str = Field(default="text", description="数据类型")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")
    timestamp: float = Field(default=0.0, description="消息时间戳")
    raw: Optional[BiliRawMessage] = Field(default=None, description="平台原始消息")

    # 类型化字段（替代原 metadata）
    user_id: Optional[str] = Field(default=None, description="用户 ID")
    user_nickname: Optional[str] = Field(default=None, description="用户昵称")
    platform: Optional[str] = Field(default=None, description="平台来源")
    room_id: Optional[str] = Field(default=None, description="直播间 ID")

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
        return cls(
            text=text,
            source=source,
            raw=raw,
            data_type=data_type,
            importance=importance,
            user_id=raw_data.get("user_id"),
            user_nickname=raw_data.get("user_nickname"),
            platform=raw_data.get("platform"),
            room_id=raw_data.get("room_id"),
        )

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        result = {
            "text": self.text,
            "source": self.source,
            "data_type": self.data_type,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "user_nickname": self.user_nickname,
            "platform": self.platform,
            "room_id": self.room_id,
        }
        if self.raw is not None:
            result["raw_data"] = self.raw.model_dump()
        return result
