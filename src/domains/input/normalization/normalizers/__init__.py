"""
数据标准化器注册表
"""

from typing import Dict, Optional, Type

from .base import DataNormalizer
from .gift_normalizer import GiftNormalizer
from .guard_normalizer import GuardNormalizer
from .superchat_normalizer import SuperChatNormalizer
from .text_normalizer import TextNormalizer


class NormalizerRegistry:
    """标准化器注册表"""

    _normalizers: Dict[str, Type[DataNormalizer]] = {}

    @classmethod
    def register(cls, normalizer_class: Type[DataNormalizer], data_type: str) -> Type[DataNormalizer]:
        """注册标准化器"""
        cls._normalizers[data_type] = normalizer_class
        return normalizer_class

    @classmethod
    def get_normalizer(cls, data_type: str) -> Optional[DataNormalizer]:
        """获取指定类型的标准化器"""
        normalizer_class = cls._normalizers.get(data_type)
        if normalizer_class:
            return normalizer_class()
        return None

    @classmethod
    def get_all(cls) -> Dict[str, Type[DataNormalizer]]:
        """获取所有已注册的标准化器"""
        return cls._normalizers.copy()


# 自动注册所有 Normalizer
NormalizerRegistry.register(GiftNormalizer, "gift")
NormalizerRegistry.register(SuperChatNormalizer, "superchat")
NormalizerRegistry.register(TextNormalizer, "text")
NormalizerRegistry.register(GuardNormalizer, "guard")


__all__ = [
    "NormalizerRegistry",
    "DataNormalizer",
    "GiftNormalizer",
    "SuperChatNormalizer",
    "TextNormalizer",
    "GuardNormalizer",
]
