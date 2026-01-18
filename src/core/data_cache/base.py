"""
数据缓存接口和配置

定义了数据缓存服务的接口、配置和异常。

缓存职责: 管理原始数据的生命周期,支持EventBus传递大对象。
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod


class CacheEvictionPolicy(str, Enum):
    """缓存淘汰策略"""

    TTL_ONLY = "ttl_only"  # 仅按TTL淘汰
    LRU_ONLY = "lru_only"  # 仅按LRU淘汰
    TTL_OR_LRU = "ttl_or_lru"  # TTL或LRU任一触发
    TTL_AND_LRU = "ttl_and_lru"  # TTL和LRU都触发


@dataclass
class CacheConfig:
    """
    缓存配置

    Attributes:
        ttl_seconds: TTL默认值(秒),默认300(5分钟)
        max_size_mb: 最大缓存大小(MB),默认100MB
        max_entries: 最多条目数,默认1000
        eviction_policy: 淘汰策略,默认TTL_OR_LRU
    """

    ttl_seconds: int = 300
    max_size_mb: int = 100
    max_entries: int = 1000
    eviction_policy: CacheEvictionPolicy = CacheEvictionPolicy.TTL_OR_LRU


@dataclass
class CacheStats:
    """
    缓存统计信息

    Attributes:
        hits: 命中次数
        misses: 未命中次数
        evictions: 淘汰次数
        current_size_mb: 当前大小(MB)
        current_entries: 当前条目数
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_size_mb: float = 0
    current_entries: int = 0


class NotFoundError(Exception):
    """缓存数据未找到或已过期"""

    pass


class CapacityError(Exception):
    """缓存已满,无法存储"""

    pass


class DataCache(ABC):
    """
    数据缓存服务(管理原始数据的生命周期)

    职责:
    - 存储原始数据(图片、音频等大对象)
    - 支持TTL(生存时间)
    - 支持LRU(最近最少使用)淘汰
    - 线程安全(协程锁 + 线程锁)
    - 统计信息(命中率、错误率)
    - 标签查询(按标签查找数据)
    """

    @abstractmethod
    async def store(self, data: Any, ttl: Optional[int] = None, tags: Optional[Dict[str, str]] = None) -> str:
        """
        存储原始数据

        Args:
            data: 原始数据(bytes, Image, Audio等)
            ttl: 生存时间(秒),默认使用配置的ttl_seconds
            tags: 标签(可用于查询和分类)

        Returns:
            数据引用(如 "cache://image/abc123")

        Raises:
            CapacityError: 缓存已满,无法存储
        """
        pass

    @abstractmethod
    async def retrieve(self, data_ref: str) -> Any:
        """
        根据引用获取原始数据

        Args:
            data_ref: 数据引用

        Returns:
            原始数据

        Raises:
            NotFoundError: 数据不存在或已过期
        """
        pass

    @abstractmethod
    async def delete(self, data_ref: str) -> bool:
        """
        删除数据

        Args:
            data_ref: 数据引用

        Returns:
            是否删除成功(数据存在)
        """
        pass

    @abstractmethod
    async def clear(self):
        """
        清空所有缓存
        """
        pass

    @abstractmethod
    def get_stats(self) -> CacheStats:
        """
        获取缓存统计信息

        Returns:
            缓存统计信息
        """
        pass

    @abstractmethod
    async def find_by_tags(self, tags: Dict[str, str]) -> List[str]:
        """
        根据标签查找数据引用

        Args:
            tags: 标签(完全匹配)

        Returns:
            数据引用列表
        """
        pass
