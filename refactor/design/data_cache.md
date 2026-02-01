# DataCache设计

## 🎯 核心目标

为Layer 2（输入标准化层）提供原始数据缓存服务，支持元数据传递和生命周期管理，避免EventBus传递大对象。

---

## 📊 设计概览

### 1. 设计背景

**问题**：
- Layer 2统一转Text，但某些场景（如图像输入）需要保留原始数据
- EventBus传递原始大对象（图像、音频）会影响性能
- 需要按需加载，避免内存浪费

**解决方案**：
- NormalizedText包含data_ref（引用）而非原始数据
- 原始数据存储在DataCache中
- 通过引用按需加载

### 2. 设计原则

1. **性能优化**：EventBus只传递轻量级对象
2. **生命周期管理**：自动过期，避免内存泄漏
3. **按需加载**：只在需要时从缓存获取
4. **易于测试**：接口可mock

---

## 🏗️ 接口设计

### DataCache接口

```python
from typing import Optional, Any, Dict, List
from dataclasses import dataclass
from enum import Enum
import time

class CacheEvictionPolicy(str, Enum):
    """缓存淘汰策略"""
    TTL_ONLY = "ttl_only"          # 仅按TTL淘汰
    LRU_ONLY = "lru_only"          # 仅按LRU淘汰
    TTL_OR_LRU = "ttl_or_lru"      # TTL或LRU任一触发
    TTL_AND_LRU = "ttl_and_lru"    # TTL和LRU都触发

@dataclass
class CacheConfig:
    """缓存配置"""
    ttl_seconds: int = 300                 # TTL默认5分钟
    max_size_mb: int = 100                # 最大100MB
    max_entries: int = 1000                # 最多1000个条目
    eviction_policy: CacheEvictionPolicy = CacheEvictionPolicy.TTL_OR_LRU

@dataclass
class CacheStats:
    """缓存统计"""
    hits: int = 0              # 命中次数
    misses: int = 0            # 未命中次数
    evictions: int = 0         # 淘汰次数
    current_size_mb: float = 0  # 当前大小（MB）
    current_entries: int = 0    # 当前条目数

class NotFoundError(Exception):
    """缓存数据未找到或已过期"""
    pass

class CapacityError(Exception):
    """缓存已满，无法存储"""
    pass

class DataCache(Protocol):
    """数据缓存服务（管理原始数据的生命周期）"""

    async def store(
        self,
        data: Any,
        ttl: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None
    ) -> str:
        """
        存储原始数据

        Args:
            data: 原始数据（bytes, Image, Audio等）
            ttl: 生存时间（秒），默认使用配置的ttl_seconds
            tags: 标签（可用于查询和分类）

        Returns:
            数据引用（如 "cache://image/abc123"）

        Raises:
            CapacityError: 缓存已满，无法存储
        """
        ...

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
        ...

    async def delete(self, data_ref: str) -> bool:
        """
        删除数据

        Args:
            data_ref: 数据引用

        Returns:
            是否删除成功（数据存在）
        """
        ...

    async def clear(self):
        """清空所有缓存"""
        ...

    def get_stats(self) -> CacheStats:
        """获取缓存统计信息"""
        ...

    async def find_by_tags(self, tags: Dict[str, str]) -> List[str]:
        """
        根据标签查找数据引用

        Args:
            tags: 标签（完全匹配）

        Returns:
            数据引用列表
        """
        ...
```

### NormalizedText结构

```python
from dataclasses import dataclass
from typing import Optional, Any, Dict

@dataclass
class NormalizedText:
    """标准化文本"""
    text: str                    # 文本描述
    metadata: Dict[str, Any]      # 元数据（必需）
    data_ref: Optional[str] = None  # 原始数据引用（可选）

    # 示例：图像输入
    # NormalizedText(
    #     text="用户发送了一张猫咪图片",
    #     metadata={
    #         "type": "image",
    #         "format": "jpeg",
    #         "size": 102400,
    #         "timestamp": 1234567890
    #     },
    #     data_ref="cache://image/abc123"  # 引用，不是实际数据
    # )

    # 示例：文本输入（不需要保留原始数据）
    # NormalizedText(
    #     text="用户说：你好",
    #     metadata={
    #         "type": "text",
    #         "source": "danmaku",
    #         "timestamp": 1234567890
    #     },
    #     data_ref=None
    # )
```

---

## 💾 实现示例

### MemoryDataCache实现

```python
import asyncio
import hashlib
from typing import Dict, List, Optional, Any
from collections import OrderedDict

class MemoryDataCache:
    """内存实现的数据缓存"""

    def __init__(self, config: CacheConfig):
        self.config = config
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._async_lock = asyncio.Lock()  # 协程锁
        self._thread_lock = threading.Lock()  # 线程锁（双重保护）
        self._stats = CacheStats()

        # 启动后台清理任务
        asyncio.create_task(self._cleanup_loop())

    @dataclass
    class CacheEntry:
        data: Any
        size_bytes: int
        created_at: float
        ttl: int
        tags: Dict[str, str]
        access_count: int = 0
        last_access_at: float = 0

    async def store(
        self,
        data: Any,
        ttl: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None
    ) -> str:
        async with self._lock:
            # 1. 检查容量
            data_size = self._estimate_size(data)
            if not await self._check_capacity(data_size):
                raise CapacityError(f"Cache full, cannot store {data_size} bytes")

            # 2. 生成引用
            data_ref = self._generate_ref(data)

            # 3. 存储数据
            entry = self.CacheEntry(
                data=data,
                size_bytes=data_size,
                created_at=time.time(),
                ttl=ttl or self.config.ttl_seconds,
                tags=tags or {},
                last_access_at=time.time()
            )

            self._cache[data_ref] = entry
            self._update_stats_on_store(data_size)

            return data_ref

    async def retrieve(self, data_ref: str) -> Any:
        # 使用asyncio锁（协程级别）
        async with self._async_lock:
            return self._retrieve_sync(data_ref)

    def _retrieve_sync(self, data_ref: str) -> Any:
        # 使用thread锁（线程级别）
        with self._thread_lock:
            # 1. 检查是否存在
            entry = self._cache.get(data_ref)
            if entry is None:
                self._stats.misses += 1
                raise NotFoundError(f"Data not found: {data_ref}")

            # 2. 检查是否过期
            if self._is_expired(entry):
                del self._cache[data_ref]
                self._stats.misses += 1
                raise NotFoundError(f"Data expired: {data_ref}")

            # 3. 更新访问信息（用于LRU）
            entry.access_count += 1
            entry.last_access_at = time.time()
            self._cache.move_to_end(data_ref)  # LRU: 移到最后

            self._stats.hits += 1
            return entry.data

    async def delete(self, data_ref: str) -> bool:
        async with self._lock:
            entry = self._cache.pop(data_ref, None)
            if entry:
                self._update_stats_on_delete(entry.size_bytes)
                return True
            return False

    async def clear(self):
        async with self._lock:
            self._cache.clear()
            self._stats = CacheStats()

    def get_stats(self) -> CacheStats:
        async with self._lock:
            self._update_stats_size()
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                current_size_mb=self._stats.current_size_mb,
                current_entries=len(self._cache)
            )

    async def find_by_tags(self, tags: Dict[str, str]) -> List[str]:
        async with self._lock:
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

        策略：
        - bytes: 直接对数据求hash
        - str: 对utf-8编码后求hash
        - 其他类型: 使用UUID + 类型标识
        """
        import uuid

        if isinstance(data, bytes):
            hash_input = data
            prefix = "bytes"
        elif isinstance(data, str):
            hash_input = data.encode()
            prefix = "str"
        else:
            # 对于其他对象，生成随机UUID + 类型标识
            type_id = type(data).__name__
            hash_input = f"{type_id}:{uuid.uuid4()}".encode()
            prefix = type_id

        hash_str = hashlib.sha256(hash_input).hexdigest()[:12]
        return f"cache://{prefix}/{hash_str}"

    def _estimate_size(self, data: Any) -> int:
        """估算数据大小（字节）"""
        if isinstance(data, bytes):
            return len(data)
        elif isinstance(data, str):
            return len(data.encode())
        else:
            # 其他类型估算为1KB
            return 1024

    async def _check_capacity(self, new_size: int) -> bool:
        """检查容量，必要时淘汰旧数据"""
        stats = await self.get_stats()

        # 检查条目数
        if stats.current_entries >= self.config.max_entries:
            return await self._evict_by_policy()

        # 检查大小
        if stats.current_size_mb * 1024 * 1024 + new_size > self.config.max_size_mb * 1024 * 1024:
            return await self._evict_by_policy()

        return True

    async def _evict_by_policy(self) -> bool:
        """根据策略淘汰数据"""
        policy = self.config.eviction_policy

        if policy == CacheEvictionPolicy.TTL_ONLY:
            return await self._evict_expired()
        elif policy == CacheEvictionPolicy.LRU_ONLY:
            return await self._evict_lru()
        elif policy == CacheEvictionPolicy.TTL_OR_LRU:
            # 尝试先淘汰过期的
            if await self._evict_expired():
                return True
            # 如果还不够，淘汰LRU
            return await self._evict_lru()
        elif policy == CacheEvictionPolicy.TTL_AND_LRU:
            # 只淘汰既过期又是LRU的
            return await self._evict_expired_and_lru()

        return False

    async def _evict_expired(self) -> bool:
        """淘汰过期数据"""
        expired_refs = []
        for ref, entry in self._cache.items():
            if self._is_expired(entry):
                expired_refs.append(ref)

        for ref in expired_refs:
            entry = self._cache.pop(ref)
            self._stats.evictions += 1
            self._update_stats_on_delete(entry.size_bytes)

        return len(expired_refs) > 0

    async def _evict_lru(self) -> bool:
        """淘汰最久未使用的数据（LRU）"""
        if not self._cache:
            return False

        # OrderedDict的第一个元素是最久未使用的
        ref, entry = self._cache.popitem(last=False)
        self._stats.evictions += 1
        self._update_stats_on_delete(entry.size_bytes)
        return True

    async def _evict_expired_and_lru(self) -> bool:
        """淘汰既过期又是最久未使用的数据"""
        # 找到所有过期数据中最久未使用的
        expired_refs = []
        for ref, entry in self._cache.items():
            if self._is_expired(entry):
                expired_refs.append((ref, entry.last_access_at))

        if not expired_refs:
            return False

        # 按last_access_at排序，淘汰最久未使用的
        expired_refs.sort(key=lambda x: x[1])
        ref, _ = expired_refs[0]

        entry = self._cache.pop(ref)
        self._stats.evictions += 1
        self._update_stats_on_delete(entry.size_bytes)
        return True

    def _is_expired(self, entry: CacheEntry) -> bool:
        """检查是否过期"""
        return time.time() - entry.created_at > entry.ttl

    def _update_stats_on_store(self, size_bytes: int):
        """更新统计信息（存储）"""
        self._stats.current_entries = len(self._cache)
        self._stats.current_size_mb = sum(e.size_bytes for e in self._cache.values()) / (1024 * 1024)

    def _update_stats_on_delete(self, size_bytes: int):
        """更新统计信息（删除）"""
        self._stats.current_entries = len(self._cache)
        self._stats.current_size_mb = sum(e.size_bytes for e in self._cache.values()) / (1024 * 1024)

    def _update_stats_size(self):
        """更新统计信息大小"""
        self._stats.current_size_mb = sum(e.size_bytes for e in self._cache.values()) / (1024 * 1024)

    async def _cleanup_loop(self):
        """后台清理循环"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟清理一次
                await self._evict_expired()
            except Exception as e:
                # 记录错误，不中断循环
                print(f"Cache cleanup error: {e}")
```

---

## 📋 配置示例

### DataCache配置

```toml
[data_cache]
# TTL默认5分钟
ttl_seconds = 300

# 最大100MB
max_size_mb = 100

# 最多1000个条目
max_entries = 1000

# 淘汰策略：TTL或LRU任一触发
eviction_policy = "ttl_or_lru"  # ttl_only | lru_only | ttl_or_lru | ttl_and_lru
```

---

## 🔄 使用示例

### Layer 2（Normalization）使用DataCache

```python
class Normalizer:
    """输入标准化层"""

    def __init__(self, event_bus: EventBus, data_cache: DataCache):
        self.event_bus = event_bus
        self.data_cache = data_cache

    async def normalize(self, raw_data: RawData) -> NormalizedText:
        """标准化原始数据"""

        # 1. 转换为文本
        text = await self._to_text(raw_data.content)

        # 2. 如果需要保留原始数据，放入缓存
        data_ref = None
        if raw_data.preserve_original:
            data_ref = await self.data_cache.store(
                data=raw_data.original_data,
                ttl=300,  # 5分钟
                tags={
                    "type": raw_data.type,
                    "source": raw_data.source
                }
            )

        # 3. 创建NormalizedText
        normalized = NormalizedText(
            text=text,
            metadata={
                "type": raw_data.type,
                "source": raw_data.source,
                "timestamp": raw_data.timestamp
            },
            data_ref=data_ref
        )

        # 4. 发布事件（只传递NormalizedText，不传递原始数据）
        await self.event_bus.emit("normalization.text.ready", {
            "normalized": normalized
        })

        return normalized
```

### Layer 5（Understanding）使用DataCache

```python
class Understanding:
    """表现理解层"""

    def __init__(self, event_bus: EventBus, data_cache: DataCache):
        self.event_bus = event_bus
        self.data_cache = data_cache

    async def on_text_ready(self, event: dict):
        """处理文本就绪事件"""
        normalized: NormalizedText = event.get("normalized")

        # 1. 处理文本
        text = normalized.text
        metadata = normalized.metadata

        # 2. 如果需要访问原始数据，通过引用获取
        image_features = None
        if normalized.data_ref:
            try:
                original_data = await self.data_cache.retrieve(normalized.data_ref)
                # 使用原始数据进行多模态处理
                image_features = await self._extract_image_features(original_data)
            except NotFoundError:
                # 数据已过期，使用文本处理
                self.logger.warning(f"Original data expired: {normalized.data_ref}")
                image_features = None

        # 3. 生成Intent
        intent = await self._generate_intent(text, metadata, image_features)

        # 4. 发布事件
        await self.event_bus.emit("understanding.intent.ready", {
            "intent": intent
        })

    async def _extract_image_features(self, image_data: Any):
        """提取图像特征"""
        # 实现多模态处理逻辑
        pass
```

---

## ✅ 关键优势

### 1. 性能优化
- ✅ EventBus只传递轻量级的NormalizedText对象
- ✅ 原始数据存储在DataCache中，不占用EventBus带宽
- ✅ 按需加载，只有需要时才从缓存中获取

### 2. 生命周期管理
- ✅ DataCache自动管理原始数据的生命周期（TTL过期自动删除）
- ✅ 避免内存泄漏
- ✅ 可配置的TTL，适应不同场景

### 3. 灵活性
- ✅ 不需要保留原始数据时，data_ref=None，不占用缓存
- ✅ 需要保留时，通过data_ref按需加载
- ✅ 支持多种数据类型（bytes, Image, Audio等）
- ✅ 支持标签查询，便于批量查找

### 4. 可测试性
- ✅ DataCache可以mock，易于单元测试
- ✅ NormalizedText是纯数据结构，易于验证

---

## 🔗 相关文档

- [7层架构设计](./layer_refactoring.md)
- [多Provider并发设计](./multi_provider.md)
- [插件系统设计](./plugin_system.md)
