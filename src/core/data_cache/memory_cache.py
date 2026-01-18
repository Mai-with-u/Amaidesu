"""
内存数据缓存实现

实现了MemoryDataCache类,支持:
- TTL(生存时间)过期机制
- LRU(最近最少使用)淘汰策略
- 混合淘汰策略 (TTL_OR_LRU, TTL_AND_LRU)
- 线程安全 (asyncio.Lock + threading.Lock双重保护)
- 统计功能 (hits, misses, evictions, size)
- 标签查询
- 后台清理循环
"""

import asyncio
import hashlib
import threading
import time
import uuid
from typing import Dict, List, Optional, Any
from collections import OrderedDict
from dataclasses import dataclass

from .base import DataCache, CacheConfig, CacheStats, CacheEvictionPolicy, NotFoundError, CapacityError


class MemoryDataCache(DataCache):
    """
    内存实现的数据缓存

    特性:
    - 支持TTL(生存时间)过期
    - 支持LRU(最近最少使用)淘汰
    - 支持多种淘汰策略
    - 线程安全(协程锁 + 线程锁双重保护)
    - 完善的统计功能
    - 标签查询支持
    - 后台自动清理
    """

    def __init__(self, config: Optional[CacheConfig] = None):
        """
        初始化缓存

        Args:
            config: 缓存配置，默认使用默认配置
        """
        self.config = config or CacheConfig()
        self._cache: "OrderedDict[str, MemoryDataCache._CacheEntry]" = OrderedDict()
        self._async_lock = asyncio.Lock()  # 协程锁
        self._thread_lock = threading.Lock()  # 线程锁(双重保护)
        self._stats = CacheStats()
        self.logger = None  # 延迟初始化logger

        # 启动后台清理任务
        self._cleanup_task = None
        self._is_running = True

    @dataclass
    class _CacheEntry:
        """
        缓存条目

        Attributes:
            data: 原始数据
            size_bytes: 数据大小(字节)
            created_at: 创建时间(Unix时间戳)
            ttl: 生存时间(秒)
            tags: 标签字典
            access_count: 访问次数
            last_access_at: 最后访问时间(Unix时间戳)
        """

        data: Any
        size_bytes: int
        created_at: float
        ttl: int
        tags: Dict[str, str]
        access_count: int = 0
        last_access_at: float = 0

    def _get_logger(self):
        """
        延迟初始化logger
        """
        if self.logger is None:
            from src.utils.logger import get_logger

            self.logger = get_logger("DataCache")
        return self.logger

    async def start_cleanup(self):
        """
        启动后台清理循环
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self):
        """
        停止后台清理循环
        """
        self._is_running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def store(self, data: Any, ttl: Optional[int] = None, tags: Optional[Dict[str, str]] = None) -> str:
        """
        存储原始数据

        Args:
            data: 原始数据(bytes, Image, Audio等)
            ttl: 生存时间(秒)，默认使用配置的ttl_seconds
            tags: 标签(可用于查询和分类)

        Returns:
            数据引用(如 "cache://image/abc123")

        Raises:
            CapacityError: 缓存已满，无法存储
        """
        async with self._async_lock:
            return self._store_sync(data, ttl, tags)

    def _store_sync(self, data: Any, ttl: Optional[int] = None, tags: Optional[Dict[str, str]] = None) -> str:
        """
        同步存储(内部使用)

        Args:
            data: 原始数据
            ttl: 生存时间
            tags: 标签

        Returns:
            数据引用

        Raises:
            CapacityError: 缓存已满
        """
        with self._thread_lock:
            logger = self._get_logger()

            # 1. 检查容量，必要时淘汰旧数据
            data_size = self._estimate_size(data)
            if not self._check_capacity_sync(data_size):
                raise CapacityError(
                    f"Cache full (size_mb={self._get_current_size_sync() / 1024 / 1024:.2f}, "
                    f"max_size_mb={self.config.max_size_mb}), "
                    f"cannot store {data_size} bytes"
                )

            # 2. 生成引用
            data_ref = self._generate_ref(data)

            # 3. 存储数据
            entry = self._CacheEntry(
                data=data,
                size_bytes=data_size,
                created_at=time.time(),
                ttl=ttl or self.config.ttl_seconds,
                tags=tags or {},
                last_access_at=time.time(),
            )

            self._cache[data_ref] = entry
            self._update_stats_on_store(data_size)
            logger.debug(f"Stored data: {data_ref} (size: {data_size} bytes)")

            return data_ref

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
        async with self._async_lock:
            return self._retrieve_sync(data_ref)

    def _retrieve_sync(self, data_ref: str) -> Any:
        """
        同步检索(内部使用)

        Args:
            data_ref: 数据引用

        Returns:
            原始数据

        Raises:
            NotFoundError: 数据不存在或已过期
        """
        with self._thread_lock:
            logger = self._get_logger()

            # 1. 检查是否存在
            entry = self._cache.get(data_ref)
            if entry is None:
                self._stats.misses += 1
                raise NotFoundError(f"Data not found: {data_ref}")

            # 2. 检查是否过期
            if self._is_expired(entry):
                del self._cache[data_ref]
                self._stats.misses += 1
                logger.warning(f"Data expired: {data_ref}")
                raise NotFoundError(f"Data expired: {data_ref}")

            # 3. 更新访问信息(用于LRU)
            entry.access_count += 1
            entry.last_access_at = time.time()
            self._cache.move_to_end(data_ref)  # LRU: 移到最后

            self._stats.hits += 1
            logger.debug(f"Retrieved data: {data_ref}")
            return entry.data

    async def delete(self, data_ref: str) -> bool:
        """
        删除数据

        Args:
            data_ref: 数据引用

        Returns:
            是否删除成功(数据存在)
        """
        async with self._async_lock:
            return self._delete_sync(data_ref)

    def _delete_sync(self, data_ref: str) -> bool:
        """
        同步删除(内部使用)

        Args:
            data_ref: 数据引用

        Returns:
            是否删除成功
        """
        with self._thread_lock:
            entry = self._cache.pop(data_ref, None)
            if entry:
                self._update_stats_on_delete(entry.size_bytes)
                return True
            return False

    async def clear(self):
        """
        清空所有缓存
        """
        async with self._async_lock:
            with self._thread_lock:
                self._cache.clear()
                self._stats = CacheStats()
                self._get_logger().info("Cache cleared")

    def get_stats(self) -> CacheStats:
        """
        获取缓存统计信息

        Returns:
            缓存统计信息
        """
        with self._thread_lock:
            self._update_stats_size_sync()
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                current_size_mb=self._stats.current_size_mb,
                current_entries=len(self._cache),
            )

    async def find_by_tags(self, tags: Dict[str, str]) -> List[str]:
        """
        根据标签查找数据引用

        Args:
            tags: 标签(完全匹配)

        Returns:
            数据引用列表
        """
        async with self._async_lock:
            with self._thread_lock:
                matches = []
                for ref, entry in self._cache.items():
                    if self._is_expired(entry):
                        continue
                    if all(entry.tags.get(k) == v for k, v in tags.items()):
                        matches.append(ref)
                return matches

    # ========== 私有方法 ==========

    def _generate_ref(self, data: Any) -> str:
        """
        生成数据引用

        Args:
            data: 原始数据

        Returns:
            数据引用
        """
        if isinstance(data, bytes):
            hash_input = data
            prefix = "bytes"
        elif isinstance(data, str):
            hash_input = data.encode()
            prefix = "str"
        else:
            type_id = type(data).__name__
            hash_input = f"{type_id}:{uuid.uuid4()}".encode()
            prefix = type_id

        hash_str = hashlib.sha256(hash_input).hexdigest()[:12]
        return f"cache://{prefix}/{hash_str}"

    def _estimate_size(self, data: Any) -> int:
        """
        估算数据大小(字节)

        Args:
            data: 原始数据

        Returns:
            大小(字节)
        """
        if isinstance(data, bytes):
            return len(data)
        elif isinstance(data, str):
            return len(data.encode())
        else:
            return 1024  # 其他类型估算为1KB

    def _check_capacity_sync(self, new_size: int) -> bool:
        """
        检查容量，必要时淘汰旧数据(同步)

        Args:
            new_size: 新数据大小

        Returns:
            是否可以存储
        """
        # 检查条目数
        if len(self._cache) >= self.config.max_entries:
            return self._evict_by_policy_sync()

        # 检查大小
        current_size_mb = self._get_current_size_sync()
        if current_size_mb * 1024 * 1024 + new_size > self.config.max_size_mb * 1024 * 1024:
            return self._evict_by_policy_sync()

        return True

    def _evict_by_policy_sync(self) -> bool:
        """
        根据策略淘汰数据(同步)

        Returns:
            是否成功淘汰(可以继续存储)
        """
        policy = self.config.eviction_policy

        if policy == CacheEvictionPolicy.TTL_ONLY:
            return self._evict_expired_sync()
        elif policy == CacheEvictionPolicy.LRU_ONLY:
            return self._evict_lru_sync()
        elif policy == CacheEvictionPolicy.TTL_OR_LRU:
            if self._evict_expired_sync():
                return True
            return self._evict_lru_sync()
        elif policy == CacheEvictionPolicy.TTL_AND_LRU:
            return self._evict_expired_and_lru_sync()

        return False

    def _evict_expired_sync(self) -> bool:
        """
        淘汰过期数据(同步)

        Returns:
            是否成功淘汰
        """
        expired_refs = []
        for ref, entry in self._cache.items():
            if self._is_expired(entry):
                expired_refs.append(ref)

        for ref in expired_refs:
            entry = self._cache.pop(ref)
            self._stats.evictions += 1
            self._update_stats_on_delete(entry.size_bytes)

        return len(expired_refs) > 0

    def _evict_lru_sync(self) -> bool:
        """
        淘汰最久未使用的数据(LRU，同步)

        Returns:
            是否成功淘汰
        """
        if not self._cache:
            return False

        ref, entry = self._cache.popitem(last=False)
        self._stats.evictions += 1
        self._update_stats_on_delete(entry.size_bytes)
        return True

    def _evict_expired_and_lru_sync(self) -> bool:
        """
        淘汰既过期又是最久未使用的数据(同步)

        Returns:
            是否成功淘汰
        """
        expired_refs = []
        for ref, entry in self._cache.items():
            if self._is_expired(entry):
                expired_refs.append((ref, entry.last_access_at))

        if not expired_refs:
            return False

        # 按最后访问时间排序，淘汰最久未使用的过期数据
        expired_refs.sort(key=lambda x: x[1])
        ref, _ = expired_refs[0]

        entry = self._cache.pop(ref)
        self._stats.evictions += 1
        self._update_stats_on_delete(entry.size_bytes)
        return True

    def _is_expired(self, entry: _CacheEntry) -> bool:
        """
        检查是否过期

        Args:
            entry: 缓存条目

        Returns:
            是否过期
        """
        return time.time() - entry.created_at > entry.ttl

    def _get_current_size_sync(self) -> float:
        """
        获取当前缓存大小(同步)

        Returns:
            大小(字节)
        """
        return sum(e.size_bytes for e in self._cache.values())

    def _update_stats_on_store(self, size_bytes: int):
        """
        更新统计信息(存储)

        Args:
            size_bytes: 数据大小(字节)
        """
        self._stats.current_entries = len(self._cache)
        self._update_stats_size_sync()

    def _update_stats_on_delete(self, size_bytes: int):
        """
        更新统计信息(删除)

        Args:
            size_bytes: 数据大小(字节)
        """
        self._stats.current_entries = len(self._cache)
        self._update_stats_size_sync()

    def _update_stats_size_sync(self):
        """
        更新统计信息大小(同步)
        """
        self._stats.current_size_mb = sum(e.size_bytes for e in self._cache.values()) / (1024 * 1024)

    async def _cleanup_loop(self):
        """
        后台清理循环

        定期清理过期数据，避免缓存无限增长。
        """
        while self._is_running:
            try:
                await asyncio.sleep(60)  # 每分钟清理一次
                async with self._async_lock:
                    with self._thread_lock:
                        self._evict_expired_sync()
            except Exception as e:
                logger = self._get_logger()
                logger.error(f"Cache cleanup error: {e}", exc_info=True)
