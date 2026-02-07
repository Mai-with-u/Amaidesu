"""
RawData数据类定义

Input Domain(输入域)的输出格式，表示从外部获取的原始数据。
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import time


@dataclass
class RawData:
    """
    原始数据

    Input Domain (输入域) 的输出格式，可以包括:
    - 弹幕文本
    - 游戏事件数据
    - 语音音频数据
    - 屏幕截图数据
    等任意格式

    Attributes:
        content: 原始内容(bytes, str, dict等)
        source: 数据源标识(如"bili_danmaku", "minecraft", "console")
        data_type: 数据类型(如"text", "audio", "image", "json", "event")
        timestamp: 时间戳(Unix时间戳,秒)
        preserve_original: 是否保留原始数据到缓存
        original_data: 原始数据(如果content已经被处理过，这里保存原始数据)
        metadata: 额外的元数据(用于扩展和特殊用途)
        data_ref: DataCache引用(如果需要缓存原始数据)
    """

    content: Any
    source: str
    data_type: str
    timestamp: float = field(default_factory=time.time)
    preserve_original: bool = False
    original_data: Optional[Any] = None
    metadata: dict = field(default_factory=dict)
    data_ref: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}

    def with_data_ref(self, data_ref: str) -> "RawData":
        """
        设置DataCache引用并返回新对象

        注意：DataCache功能已移除（未实际使用），此方法保留用于向后兼容。

        Args:
            data_ref: 数据引用

        Returns:
            新的RawData对象，带有data_ref
        """
        return RawData(
            content=self.content,
            source=self.source,
            data_type=self.data_type,
            timestamp=self.timestamp,
            preserve_original=self.preserve_original,
            original_data=self.original_data,
            metadata=self.metadata.copy(),
            data_ref=data_ref,  # 保留字段，但DataCache已移除
        )

    def to_dict(self) -> dict:
        """
        转换为字典

        Returns:
            字典表示的RawData
        """
        return {
            "content": self.content,
            "source": self.source,
            "data_type": self.data_type,
            "timestamp": self.timestamp,
            "preserve_original": self.preserve_original,
            "metadata": self.metadata,
            # data_ref字段已保留，但DataCache功能已移除（未实际使用）
            "data_ref": self.data_ref,
        }
