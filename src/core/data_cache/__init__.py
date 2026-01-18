"""
数据缓存模块

导出了数据缓存服务的所有组件。
"""

from .base import DataCache, CacheConfig, CacheStats, CacheEvictionPolicy, NotFoundError, CapacityError
from .memory_cache import MemoryDataCache

__all__ = [
    # 接口
    "DataCache",
    # 配置
    "CacheConfig",
    "CacheEvictionPolicy",
    # 统计
    "CacheStats",
    # 异常
    "NotFoundError",
    "CapacityError",
    # 实现
    "MemoryDataCache",
]
