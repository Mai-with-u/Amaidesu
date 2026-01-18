"""
NormalizedText数据类定义

Layer 2(输入标准化层)的输出格式，表示标准化后的文本。
"""

from dataclasses import dataclass
from typing import Optional, Any, Dict
import time


@dataclass
class NormalizedText:
    """
    标准化文本

    Layer 2(输入标准化层)的输出格式，统一转换为文本格式。

    Attributes:
        text: 文本描述
        metadata: 元数据(必需，包含type, source, timestamp等)
        data_ref: 原始数据引用(可选，用于Layer 4按需加载原始数据)
    """

    text: str
    metadata: Dict[str, Any]
    data_ref: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}

        # 确保元数据包含基本字段
        if "type" not in self.metadata:
            self.metadata["type"] = "text"
        if "timestamp" not in self.metadata:
            self.metadata["timestamp"] = time.time()

    @property
    def source(self) -> str:
        """获取数据源"""
        return self.metadata.get("source", "unknown")

    @property
    def data_type(self) -> str:
        """获取数据类型"""
        return self.metadata.get("type", "text")

    def with_data_ref(self, data_ref: str) -> "NormalizedText":
        """
        设置DataCache引用并返回新对象

        Args:
            data_ref: 数据引用

        Returns:
            新的NormalizedText对象，带有data_ref
        """
        return NormalizedText(text=self.text, metadata=self.metadata.copy(), data_ref=data_ref)

    def to_dict(self) -> dict:
        """
        转换为字典

        Returns:
            字典表示的NormalizedText
        """
        return {"text": self.text, "metadata": self.metadata, "data_ref": self.data_ref}

    @classmethod
    def from_raw_data(
        cls, raw_data: Any, text: str, source: str, preserve_original: bool = False, data_ref: Optional[str] = None
    ) -> "NormalizedText":
        """
        从RawData创建NormalizedText

        Args:
            raw_data: 原始数据对象(RawData或dict)
            text: 标准化后的文本
            source: 数据源
            preserve_original: 是否保留原始数据
            data_ref: 数据引用(如果已缓存)

        Returns:
            NormalizedText对象
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
        metadata["type"] = "text"
        metadata["timestamp"] = time.time()

        # 如果需要保留原始数据，但data_ref未提供，设置标志
        if preserve_original and not data_ref:
            metadata["preserve_original"] = True

        return cls(text=text, metadata=metadata, data_ref=data_ref)
