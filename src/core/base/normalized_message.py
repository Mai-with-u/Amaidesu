"""
NormalizedMessage数据类型定义

Input Domain中的Normalization模块输出格式，表示标准化的消息。

核心改进：
- 保留原始结构化数据（不丢失信息）
- 提供文本描述（用于LLM处理）
- 自动计算重要性（0-1）
- 使用 Pydantic BaseModel 自动序列化
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class NormalizedMessage(BaseModel):
    """
    标准化消息（Input Domain中Normalization的输出）

    核心改进：
    - text: 用于LLM处理的文本描述
    - content: 保留原始结构化数据（不丢失信息）
    - importance: 预计算的重要性（0-1）
    - 使用 Pydantic 自动序列化/反序列化

    Attributes:
        text: 文本描述（用于LLM处理）
        content: 结构化内容（StructuredContent的任意子类）
        source: 数据来源（弹幕/控制台/等）
        data_type: 数据类型（text/gift/super_chat等）
        importance: 重要性（0-1，自动计算）
        metadata: 额外的元数据
        timestamp: 时间戳（Unix时间戳，秒）
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        # 使用枚举值而非对象
        use_enum_values=True,
    )

    text: str
    content: Any  # StructuredContent 的任意子类（避免循环导入）
    source: str
    data_type: str
    importance: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0  # 初始化时会被 model_validator 设置

    @model_validator(mode="after")
    def set_defaults_and_metadata(self) -> "NormalizedMessage":
        """初始化后处理：设置默认时间戳和元数据"""
        import time

        # 设置时间戳（如果未提供）
        if self.timestamp == 0.0:
            self.timestamp = time.time()

        # 确保元数据包含基本字段
        if "type" not in self.metadata:
            self.metadata["type"] = self.data_type
        if "timestamp" not in self.metadata:
            self.metadata["timestamp"] = self.timestamp

        return self

    @property
    def user_id(self) -> Optional[str]:
        """获取用户ID（便捷方法）"""
        return self.content.get_user_id()

    @property
    def display_text(self) -> str:
        """获取显示文本（便捷方法）"""
        return self.content.get_display_text()

    @classmethod
    def from_raw_data(
        cls,
        raw_data: Any,
        text: str,
        source: str,
        content: Any,  # StructuredContent 的任意子类
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
