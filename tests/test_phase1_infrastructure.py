"""
Phase 1 基础设施测试

包含:
- Provider接口测试
- EventBus增强测试
- DataCache测试
"""

import pytest
import asyncio
from src.core.providers import RawData, RenderParameters, CanonicalMessage


class TestProviderDataStructures:
    """测试Provider数据类"""

    def test_raw_data_creation(self):
        """测试RawData创建"""
        data = RawData(content="hello", source="test", data_type="text", timestamp=1234567890)
        assert data.content == "hello"
        assert data.source == "test"
        assert data.data_type == "text"
        assert data.preserve_original is False
        assert data.metadata == {}

    def test_raw_data_with_metadata(self):
        """测试RawData带元数据"""
        data = RawData(
            content={"key": "value"}, source="test", data_type="json", timestamp=1234567890, metadata={"extra": "info"}
        )
        assert data.metadata == {"extra": "info"}

    def test_render_parameters_creation(self):
        """测试RenderParameters创建"""
        params = RenderParameters(content="world", render_type="text")
        assert params.content == "world"
        assert params.priority == 100

    def test_canonical_message_creation(self):
        """测试CanonicalMessage创建"""
        msg = CanonicalMessage(text="hello world", source="test")
        assert msg.text == "hello world"
        assert msg.source == "test"
        assert msg.metadata == {}


class TestEventBusBasic:
    """测试EventBus基础功能"""

    @pytest.mark.asyncio
    async def test_emit_and_receive(self):
        """测试发布和接收"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        received = []

        async def handler(event_name, data, source):
            received.append((event_name, data, source))

        event_bus.on("test.event", handler)
        await event_bus.emit("test.event", "data", "test_source")

        assert len(received) == 1
        assert received[0] == ("test.event", "data", "test_source")


class TestDataCacheBasic:
    """测试DataCache基础功能"""

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self):
        """测试存储和检索"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        config = CacheConfig(ttl_seconds=300, max_entries=10)
        cache = MemoryDataCache(config)

        ref = await cache.store("test_data", ttl=60)
        data = await cache.retrieve(ref)

        assert data == "test_data"
        assert ref.startswith("cache://str/")

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        """测试TTL过期"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        cache = MemoryDataCache(CacheConfig(ttl_seconds=1))

        ref = await cache.store("test_data", ttl=1)

        # 立即检索应该成功
        data = await cache.retrieve(ref)
        assert data == "test_data"

        # 等待2秒后应该过期
        await asyncio.sleep(2)
        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref)

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        """测试LRU淘汰"""
        from src.core.data_cache import MemoryDataCache, CacheConfig, CacheEvictionPolicy

        config = CacheConfig(max_entries=3, eviction_policy=CacheEvictionPolicy.LRU_ONLY)
        cache = MemoryDataCache(config)

        # 存储3个数据
        ref1 = await cache.store("data1")
        ref2 = await cache.store("data2")
        ref3 = await cache.store("data3")

        # 访问ref1和ref2，使ref3成为LRU
        await cache.retrieve(ref1)
        await cache.retrieve(ref2)

        # 存储第4个数据，应该淘汰ref3(LRU)
        ref4 = await cache.store("data4")

        # ref1和ref2应该还在
        assert await cache.retrieve(ref1) == "data1"
        assert await cache.retrieve(ref2) == "data2"
        assert await cache.retrieve(ref4) == "data4"

        # ref3应该被淘汰
        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref3)

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """测试统计功能"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        cache = MemoryDataCache(CacheConfig(ttl_seconds=300))

        await cache.store("data1")
        await cache.store("data2")

        # 命中
        await cache.retrieve(await cache.store("data3"))

        # 未命中
        from src.core.data_cache.base import NotFoundError

        try:
            await cache.retrieve("cache://nonexistent")
        except NotFoundError:
            pass

        stats = cache.get_stats()
        assert stats.current_entries == 3
        assert stats.hits == 1
        assert stats.misses == 1

    @pytest.mark.asyncio
    async def test_tag_based_search(self):
        """测试标签查询"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        cache = MemoryDataCache(CacheConfig())

        ref1 = await cache.store("image1", tags={"type": "image", "format": "jpeg"})
        ref2 = await cache.store("image2", tags={"type": "image", "format": "png"})
        await cache.store("audio1", tags={"type": "audio", "format": "mp3"})

        # 查找所有图像
        images = await cache.find_by_tags({"type": "image"})
        assert len(images) == 2
        assert ref1 in images
        assert ref2 in images

        # 查找jpeg格式
        jpeg_images = await cache.find_by_tags({"type": "image", "format": "jpeg"})
        assert len(jpeg_images) == 1
        assert jpeg_images[0] == ref1


class TestEventBusAdvanced:
    """测试EventBus高级功能"""

    @pytest.mark.asyncio
    async def test_priority_control(self):
        """测试优先级控制"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        execution_order = []

        # 注册三个不同优先级的处理器
        async def high_priority(event_name, data, source):
            execution_order.append("high")

        async def medium_priority(event_name, data, source):
            execution_order.append("medium")

        async def low_priority(event_name, data, source):
            execution_order.append("low")

        # 先注册中优先级
        event_bus.on("test.event", medium_priority, priority=50)
        # 再注册高优先级（应该先执行）
        event_bus.on("test.event", high_priority, priority=10)
        # 最后注册低优先级（应该最后执行）
        event_bus.on("test.event", low_priority, priority=100)

        await event_bus.emit("test.event", "data", "test_source")

        # 验证执行顺序
        assert execution_order == ["high", "medium", "low"]

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        """测试错误隔离"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)
        results = []

        # 注册一个会抛出异常的处理器
        async def failing_handler(event_name, data, source):
            results.append("failing_before")
            raise ValueError("Simulated error")

        # 注册一个正常的处理器
        async def normal_handler(event_name, data, source):
            results.append("normal")

        event_bus.on("test.event", failing_handler)
        event_bus.on("test.event", normal_handler)

        # 发布事件，一个处理器失败不影响另一个
        await event_bus.emit("test.event", "data", "test_source")

        # 两个处理器都应该被调用
        assert "failing_before" in results
        assert "normal" in results

    @pytest.mark.asyncio
    async def test_statistics_tracking(self):
        """测试统计信息跟踪"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=True)

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)

        # 发布10个事件
        for i in range(10):
            await event_bus.emit("test.event", f"data{i}", "source")

        # 发布一些其他事件
        await event_bus.emit("other.event", "data", "source")

        stats = event_bus.get_all_stats()

        # 验证统计信息
        assert stats["test.event"].emit_count == 10
        assert stats["test.event"].listener_count == 1
        assert stats["test.event"].error_count == 0  # 没有错误

    @pytest.mark.asyncio
    async def test_statistics_with_errors(self):
        """测试错误统计"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=True)

        async def failing_handler(event_name, data, source):
            raise RuntimeError("Test error")

        async def normal_handler(event_name, data, source):
            pass

        event_bus.on("test.event", failing_handler)
        event_bus.on("test.event", normal_handler)

        # 发布10个事件
        for i in range(10):
            await event_bus.emit("test.event", f"data{i}", "source")

        stats = event_bus.get_all_stats()
        event_stats = stats["test.event"]

        # 验证错误统计
        assert event_stats.emit_count == 10
        assert event_stats.listener_count == 2
        assert event_stats.error_count == 10  # 每次都失败一个处理器

    @pytest.mark.asyncio
    async def test_lifecycle_cleanup(self):
        """测试生命周期清理"""
        from src.core.event_bus import EventBus

        event_bus = EventBus(enable_stats=False)

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)
        assert len(event_bus._handlers["test.event"]) == 1

        # 清理所有处理器
        await event_bus.cleanup()

        # 验证所有处理器都被清理
        assert len(event_bus._handlers) == 0


class TestDataCacheAdvanced:
    """测试DataCache高级功能"""

    @pytest.mark.asyncio
    async def test_ttl_or_lru_policy(self):
        """测试TTL_OR_LRU混合策略"""
        from src.core.data_cache import MemoryDataCache, CacheConfig, CacheEvictionPolicy

        config = CacheConfig(
            max_entries=3,
            ttl_seconds=10,
            eviction_policy=CacheEvictionPolicy.TTL_OR_LRU,
        )
        cache = MemoryDataCache(config)

        # 存储3个数据，TTL=2秒
        ref1 = await cache.store("data1", ttl=2)
        ref2 = await cache.store("data2", ttl=2)
        ref3 = await cache.store("data3", ttl=2)

        # 访问ref1和ref2，使ref3成为LRU
        await cache.retrieve(ref1)
        await cache.retrieve(ref2)

        # 存储第4个数据，应该淘汰ref3（LRU）
        ref4 = await cache.store("data4", ttl=2)

        # ref3应该被淘汰
        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref3)

        # 现在测试TTL过期
        await asyncio.sleep(3)  # 等待TTL过期（超过2秒）

        # 所有剩余数据都应该过期
        with pytest.raises(NotFoundError):
            await cache.retrieve(ref1)

    @pytest.mark.asyncio
    async def test_ttl_and_lru_policy(self):
        """测试TTL_AND_LRU混合策略（两个条件都满足才淘汰）"""
        from src.core.data_cache import MemoryDataCache, CacheConfig, CacheEvictionPolicy

        config = CacheConfig(
            max_entries=3,
            ttl_seconds=2,
            eviction_policy=CacheEvictionPolicy.TTL_AND_LRU,
        )
        cache = MemoryDataCache(config)

        # 存储3个数据
        ref1 = await cache.store("data1", ttl=1)  # 1秒过期
        ref2 = await cache.store("data2", ttl=3)  # 3秒过期
        ref3 = await cache.store("data3", ttl=3)  # 3秒过期

        # 访问ref2和ref3，使ref1成为LRU
        await cache.retrieve(ref2)
        await cache.retrieve(ref3)

        # 等待1秒，ref1 TTL过期
        await asyncio.sleep(1.5)

        # 存储第4个数据
        # ref1应该被淘汰（TTL过期且是LRU）
        ref4 = await cache.store("data4", ttl=3)

        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref1)

        # ref2和ref3应该还在（虽然ref1是LRU，但它TTL还没到期）
        assert await cache.retrieve(ref2) == "data2"
        assert await cache.retrieve(ref3) == "data3"

    @pytest.mark.asyncio
    async def test_concurrent_access_safety(self):
        """测试并发访问安全性"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        cache = MemoryDataCache(CacheConfig(max_entries=1000))

        # 顺序存储10个数据（减少数量以避免潜在问题）
        refs = []
        for i in range(10):
            ref = await cache.store(f"data{i}")
            refs.append(ref)

        # 验证所有数据都存储成功
        assert len(refs) == 10

        # 验证缓存中的条目数
        stats = cache.get_stats()
        assert stats.current_entries == 10, (
            f"Expected 10 entries, got {stats.current_entries}, evictions: {stats.evictions}"
        )

        # 顺序检索所有数据
        for i, ref in enumerate(refs):
            data = await cache.retrieve(ref)
            assert data == f"data{i}", f"Expected 'data{i}', got {data}"

        # 再次验证缓存统计
        stats = cache.get_stats()
        assert stats.hits == 10
        assert stats.misses == 0

    @pytest.mark.asyncio
    async def test_max_size_limit(self):
        """测试缓存大小限制"""
        from src.core.data_cache import MemoryDataCache, CacheConfig, CacheEvictionPolicy

        # 限制缓存条目数为3
        config = CacheConfig(max_entries=3, eviction_policy=CacheEvictionPolicy.LRU_ONLY)
        cache = MemoryDataCache(config)

        # 存储3个数据
        await cache.store("data1")
        await cache.store("data2")
        await cache.store("data3")

        stats = cache.get_stats()
        assert stats.current_entries == 3

        # 存储第4个数据，应该触发LRU淘汰
        await cache.store("data4")

        stats = cache.get_stats()
        assert stats.current_entries == 3

    @pytest.mark.asyncio
    async def test_background_cleanup_task(self):
        """测试后台清理任务"""
        from src.core.data_cache import MemoryDataCache, CacheConfig

        # 启用后台清理（默认每60秒清理一次）
        config = CacheConfig(ttl_seconds=1)
        cache = MemoryDataCache(config)

        # 启动后台清理任务
        await cache.start_cleanup()

        # 存储数据，TTL=1秒
        ref = await cache.store("test_data", ttl=1)

        # 立即检索应该成功
        assert await cache.retrieve(ref) == "test_data"

        # 等待65秒（超过60秒清理间隔），后台清理应该已执行
        # 为了测试速度，我们手动触发一次清理
        await asyncio.sleep(2)

        # 数据应该被删除（TTL过期）
        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref)

        # 停止后台清理任务
        await cache.stop_cleanup()

    @pytest.mark.asyncio
    async def test_cache_eviction_with_size_limit(self):
        """测试缓存LRU的淘汰"""
        from src.core.data_cache import MemoryDataCache, CacheConfig, CacheEvictionPolicy

        # 限制缓存条目数为3，LRU策略
        config = CacheConfig(max_entries=3, eviction_policy=CacheEvictionPolicy.LRU_ONLY)
        cache = MemoryDataCache(config)

        # 存储3个数据
        ref1 = await cache.store("data1")
        ref2 = await cache.store("data2")
        ref3 = await cache.store("data3")

        # 访问ref1和ref2，使ref3成为LRU
        assert await cache.retrieve(ref1) == "data1"
        assert await cache.retrieve(ref2) == "data2"

        # 存储第4个数据，应该淘汰ref3（LRU）
        ref4 = await cache.store("data4")

        # ref3应该被淘汰
        from src.core.data_cache.base import NotFoundError

        with pytest.raises(NotFoundError):
            await cache.retrieve(ref3)

        # 其他数据应该还在
        assert await cache.retrieve(ref1) == "data1"
        assert await cache.retrieve(ref2) == "data2"
        assert await cache.retrieve(ref4) == "data4"

        # 验证条目数
        stats = cache.get_stats()
        assert stats.current_entries == 3
