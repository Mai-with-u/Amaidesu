"""
DecisionManager单元测试

测试DecisionProviderFactory和DecisionManager的功能。
"""

import pytest
import asyncio
from src.core.decision_manager import DecisionProviderFactory, DecisionManager
from src.canonical.canonical_message import CanonicalMessage


class MockDecisionProvider:
    """Mock DecisionProvider用于测试"""

    def __init__(self, config: dict):
        self.config = config
        self.event_bus = None
        self.setup_called = False
        self.cleanup_called = False
        self.decide_count = 0

    async def setup(self, event_bus, config: dict):
        self.event_bus = event_bus
        self.config = config
        self.setup_called = True

    async def decide(self, canonical_message):
        self.decide_count += 1
        # 返回一个简单的MessageBase mock
        return None

    async def cleanup(self):
        self.cleanup_called = True

    def get_info(self):
        return {
            "name": "MockDecisionProvider",
            "version": "1.0.0",
            "description": "Mock provider for testing",
            "author": "Test",
            "api_version": "1.0",
        }


class MockEventBus:
    """Mock EventBus用于测试"""

    def __init__(self):
        self.emitted_events = []

    async def emit(self, event_name: str, data: any, source: str = "unknown"):
        self.emitted_events.append(
            {
                "name": event_name,
                "data": data,
                "source": source,
            }
        )


class TestDecisionProviderFactory:
    """测试DecisionProviderFactory"""

    def test_register_provider(self):
        """测试注册Provider"""
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)

        providers = factory.list_providers()
        assert "mock" in providers

    def test_register_provider_overwrite(self):
        """测试覆盖已注册的Provider"""
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        factory.register("mock", MockDecisionProvider)

        providers = factory.list_providers()
        assert len(providers) == 1
        assert "mock" in providers

    def test_create_provider(self):
        """测试创建Provider实例"""
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)

        config = {"test_key": "test_value"}
        provider = factory.create("mock", config)

        assert isinstance(provider, MockDecisionProvider)
        assert provider.config == config

    def test_create_provider_not_found(self):
        """测试创建不存在的Provider"""
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)

        with pytest.raises(ValueError) as exc_info:
            factory.create("not_exists", {})

        assert "not_exists" in str(exc_info.value)

    def test_list_providers(self):
        """测试列出所有Provider"""
        factory = DecisionProviderFactory()
        factory.register("provider1", MockDecisionProvider)
        factory.register("provider2", MockDecisionProvider)

        providers = factory.list_providers()

        assert len(providers) == 2
        assert "provider1" in providers
        assert "provider2" in providers


class TestDecisionManager:
    """测试DecisionManager"""

    @pytest.mark.asyncio
    async def test_setup_provider(self):
        """测试设置Provider"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        manager.set_factory(factory)

        await manager.setup("mock", {"key": "value"})

        provider = manager.get_current_provider()
        assert provider is not None
        assert isinstance(provider, MockDecisionProvider)
        assert provider.setup_called is True

    @pytest.mark.asyncio
    async def test_setup_without_factory(self):
        """测试未设置factory时的setup"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)

        with pytest.raises(ValueError) as exc_info:
            await manager.setup("mock", {})

        assert "Factory" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_setup_invalid_provider(self):
        """测试设置不存在的Provider"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        manager.set_factory(factory)

        with pytest.raises(ValueError) as exc_info:
            await manager.setup("not_exists", {})

        assert "not_exists" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decide(self):
        """测试决策功能"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        manager.set_factory(factory)

        await manager.setup("mock", {})

        canonical_message = CanonicalMessage(text="Test", source="test")
        await manager.decide(canonical_message)

        provider = manager.get_current_provider()
        assert provider.decide_count == 1

    @pytest.mark.asyncio
    async def test_decide_without_provider(self):
        """测试未设置Provider时的决策"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)

        canonical_message = CanonicalMessage(text="Test", source="test")

        with pytest.raises(RuntimeError) as exc_info:
            await manager.decide(canonical_message)

        assert "未设置DecisionProvider" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_switch_provider(self):
        """测试切换Provider"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock1", MockDecisionProvider)
        factory.register("mock2", MockDecisionProvider)
        manager.set_factory(factory)

        # 设置第一个Provider
        await manager.setup("mock1", {})

        provider1 = manager.get_current_provider()
        assert provider1 is not None

        # 切换到第二个Provider
        await manager.switch_provider("mock2", {})

        provider2 = manager.get_current_provider()
        assert provider2 is not None
        assert provider1 is not provider2
        assert provider1.cleanup_called is True

    @pytest.mark.asyncio
    async def test_switch_provider_failure(self):
        """测试切换Provider失败时的回退"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock1", MockDecisionProvider)
        factory.register("mock2", MockDecisionProvider)
        manager.set_factory(factory)

        # 设置第一个Provider
        await manager.setup("mock1", {})

        provider1 = manager.get_current_provider()
        original_cleanup = provider1.cleanup_called

        # 切换到不存在的Provider，应该回退
        with pytest.raises(ValueError):
            await manager.switch_provider("not_exists", {})

        # 验证回退到原Provider
        current_provider = manager.get_current_provider()
        assert current_provider is provider1
        assert provider1.cleanup_called == original_cleanup  # 没有额外调用cleanup

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """测试清理功能"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        manager.set_factory(factory)

        await manager.setup("mock", {})

        await manager.cleanup()

        assert manager.get_current_provider() is None
        assert manager.get_current_provider_name() is None
        assert manager._factory is None

    @pytest.mark.asyncio
    async def test_get_available_providers(self):
        """测试获取可用Provider列表"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock1", MockDecisionProvider)
        factory.register("mock2", MockDecisionProvider)
        manager.set_factory(factory)

        providers = manager.get_available_providers()

        assert len(providers) == 2
        assert "mock1" in providers
        assert "mock2" in providers

    @pytest.mark.asyncio
    async def test_get_available_providers_without_factory(self):
        """测试未设置factory时的Provider列表"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)

        providers = manager.get_available_providers()

        assert providers == []

    @pytest.mark.asyncio
    async def test_concurrent_switch(self):
        """测试并发切换Provider的安全性"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock1", MockDecisionProvider)
        factory.register("mock2", MockDecisionProvider)
        manager.set_factory(factory)

        await manager.setup("mock1", {})

        # 并发切换
        tasks = [
            manager.switch_provider("mock2", {}),
            manager.switch_provider("mock1", {}),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 所有任务都应该完成（一个成功，一个失败）
        assert len(results) == 2
        # 当前应该有一个有效的Provider
        assert manager.get_current_provider() is not None

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        """测试完整的生命周期"""
        event_bus = MockEventBus()
        manager = DecisionManager(event_bus)
        factory = DecisionProviderFactory()
        factory.register("mock", MockDecisionProvider)
        manager.set_factory(factory)

        # Setup
        await manager.setup("mock", {"test": "value"})
        provider = manager.get_current_provider()
        assert provider.setup_called is True

        # Decide
        canonical_message = CanonicalMessage(text="Test", source="test")
        await manager.decide(canonical_message)
        assert provider.decide_count == 1

        # Switch
        await manager.switch_provider("mock", {"new": "value"})
        provider = manager.get_current_provider()
        assert provider.decide_count == 1  # 重置了

        # Cleanup
        await manager.cleanup()
        assert provider.cleanup_called is True
        assert manager.get_current_provider() is None
