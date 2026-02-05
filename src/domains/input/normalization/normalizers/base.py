"""
数据标准化器基类
"""

from abc import ABC, abstractmethod
from typing import Optional
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage


class DataNormalizer(ABC):
    """数据标准化器接口"""

    @abstractmethod
    def can_handle(self, data_type: str) -> bool:
        """判断是否能处理该数据类型"""
        pass

    @abstractmethod
    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        """将 RawData 转换为 NormalizedMessage"""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """优先级（数字越大越优先）"""
        pass
