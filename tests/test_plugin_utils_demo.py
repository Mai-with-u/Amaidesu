"""
测试工具模块的演示测试

验证test_plugin_utils.py中提供的工具是否正常工作。
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tests.test_plugin_utils import (
    MockProvider,
    MockInputProvider,
    MockDecisionProvider,
    MockEventBus,
    PluginTestBase,
    ProviderTestHelper,
    assert_plugin_info_valid,
    assert_provider_implements_protocol,
)
from src.core.data_types.raw_data import RawData


# ============================================================================
# 测试Mock工具
# ============================================================================


class TestMockProvider:
    """测试MockProvider"""

    @pytest.mark.asyncio
    async def test_mock_provider_setup_and_cleanup(self):
        """测试MockProvider的setup和cleanup"""
        provider = MockProvider("test_type")

        event_bus = MockEventBus()
        config = {"test": "config"}

        await provider.setup(event_bus, config)

        assert provider.setup_called is True
        assert provider.config == config

        await provider.cleanup()

        assert provider.cleanup_called is True


class TestMockEventBus:
    """测试MockEventBus"""

    @pytest.mark.asyncio
    async def test_emit_and_on(self):
        """测试发布和订阅"""
        event_bus = MockEventBus()
        received = []

        async def handler(event_name, data, source):
            received.append((event_name, data, source))

        event_bus.on("test.event", handler)
        await event_bus.emit("test.event", "data", "source")

        assert len(received) == 1
        assert received[0] == ("test.event", "data", "source")

    def test_get_listeners_count(self):
        """测试获取监听器数量"""
        event_bus = MockEventBus()

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)
        assert event_bus.get_listeners_count("test.event") == 1

    def test_verify_emit_called(self):
        """测试verify_emit_called"""
        event_bus = MockEventBus()

        event_bus._emits = [
            {"event": "test.event", "data": "data1", "source": "source1"},
            {"event": "other.event", "data": "data2", "source": "source2"},
        ]

        assert event_bus.verify_emit_called("test.event") is True
        assert event_bus.verify_emit_called("other.event") is True
        assert event_bus.verify_emit_called("nonexistent.event") is False
        assert event_bus.verify_emit_called("test.event", data="data1") is True
        assert event_bus.verify_emit_called("test.event", data="data2") is False

    def test_clear(self):
        """测试清除"""
        event_bus = MockEventBus()

        async def handler(event_name, data, source):
            pass

        event_bus.on("test.event", handler)
        event_bus._emits = [{"event": "test.event", "data": "data", "source": "source"}]

        assert len(event_bus._handlers) > 0
        assert len(event_bus._emits) > 0

        event_bus.clear()

        assert len(event_bus._handlers) == 0
        assert len(event_bus._emits) == 0


# ============================================================================
# 测试MockInputProvider
# ============================================================================


class TestMockInputProvider:
    """测试MockInputProvider"""

    @pytest.mark.asyncio
    async def test_collect_data(self):
        """测试采集数据"""
        test_data = [
            RawData(content="data1", source="test", data_type="text", timestamp=1234567890),
            RawData(content="data2", source="test", data_type="text", timestamp=1234567891),
        ]

        provider = MockInputProvider(config={}, test_data=test_data)

        collected_data = []
        async for data in provider._collect_data():
            collected_data.append(data)

        assert len(collected_data) == 2
        assert collected_data[0].content == "data1"
        assert collected_data[1].content == "data2"


# ============================================================================
# 测试MockDecisionProvider
# ============================================================================


class TestMockDecisionProvider:
    """测试MockDecisionProvider"""

    @pytest.mark.asyncio
    async def test_decide(self):
        """测试决策"""
        from src.canonical.canonical_message import CanonicalMessage

        provider = MockDecisionProvider(config={})
        canonical_msg = CanonicalMessage(text="test message", source="test")

        result = await provider.decide(canonical_msg)

        assert len(provider.decide_calls) == 1
        assert provider.decide_calls[0] == canonical_msg
        assert result is not None


# ============================================================================
# 测试PluginTestBase
# ============================================================================


class TestPluginTestBase(PluginTestBase):
    """测试PluginTestBase基类功能"""

    @pytest.mark.asyncio
    async def test_plugin_info_validation(self):
        """测试Plugin信息验证"""

        class MockPlugin:
            def get_info(self):
                return {
                    "name": "TestPlugin",
                    "version": "1.0.0",
                    "author": "Test Author",
                    "description": "Test plugin",
                    "category": "input",
                    "api_version": "1.0",
                }

        plugin = MockPlugin()
        await self.verify_plugin_info(plugin)

    @pytest.mark.asyncio
    async def test_provider_lifecycle_verification(self):
        """测试Provider生命周期验证"""
        event_bus = self.event_bus

        providers = [
            MockProvider("input"),
            MockProvider("output"),
        ]

        # 模拟setup
        for provider in providers:
            await provider.setup(event_bus, {})

        await self.verify_provider_lifecycle(providers)

    @pytest.mark.asyncio
    async def test_plugin_cleanup_verification(self):
        """测试Plugin清理验证"""

        class MockPlugin:
            def __init__(self, config):
                self.config = config

            async def setup(self, event_bus, config):
                providers = [MockProvider("test")]
                for provider in providers:
                    await provider.setup(event_bus, {})
                return providers

            async def cleanup(self):
                # 清理providers
                for provider in self._providers:
                    await provider.cleanup()

        plugin = MockPlugin({"enabled": True})
        providers = await plugin.setup(self.event_bus, plugin.config)
        plugin._providers = providers  # 设置providers引用

        await self.verify_plugin_cleanup(plugin, providers)


# ============================================================================
# 测试ProviderTestHelper
# ============================================================================


class TestProviderTestHelper:
    """测试ProviderTestHelper"""

    @pytest.mark.asyncio
    async def test_verify_input_provider_lifecycle(self):
        """测试验证InputProvider生命周期"""
        test_data = [
            RawData(content="data1", source="test", data_type="text", timestamp=1234567890),
            RawData(content="data2", source="test", data_type="text", timestamp=1234567891),
        ]

        provider = MockInputProvider(config={}, test_data=test_data)

        await ProviderTestHelper.verify_input_provider_lifecycle(provider, test_data)

    @pytest.mark.asyncio
    async def test_verify_decision_provider_lifecycle(self):
        """测试验证DecisionProvider生命周期"""
        event_bus = MockEventBus()
        provider = MockDecisionProvider(config={})

        await ProviderTestHelper.verify_decision_provider_lifecycle(provider, event_bus)

    def test_create_mock_message_base(self):
        """测试创建mock MessageBase"""
        msg = ProviderTestHelper.create_mock_message_base("test text")

        assert msg is not None
        assert msg.message_segment.data == "test text"

    def test_create_raw_data(self):
        """测试创建RawData"""
        data = ProviderTestHelper.create_raw_data("content", "source", "text")

        assert isinstance(data, RawData)
        assert data.content == "content"
        assert data.source == "source"
        assert data.data_type == "text"


# ============================================================================
# 测试辅助断言函数
# ============================================================================


class TestAssertionHelpers:
    """测试辅助断言函数"""

    def test_assert_plugin_info_valid(self):
        """测试assert_plugin_info_valid"""
        valid_info = {
            "name": "TestPlugin",
            "version": "1.0.0",
            "author": "Test Author",
            "description": "Test plugin",
            "category": "input",
            "api_version": "1.0",
        }

        # 不应该抛出异常
        assert_plugin_info_valid(valid_info)

    def test_assert_plugin_info_valid_missing_fields(self):
        """测试assert_plugin_info_valid缺少字段"""
        invalid_info = {
            "name": "TestPlugin",
            # 缺少version
        }

        # 应该抛出断言错误
        with pytest.raises(AssertionError):
            assert_plugin_info_valid(invalid_info)

    def test_assert_provider_implements_protocol(self):
        """测试assert_provider_implements_protocol"""
        config = {}

        # 测试InputProvider
        input_provider = MockInputProvider(config)
        assert_provider_implements_protocol(input_provider, "input")

        # 测试DecisionProvider
        decision_provider = MockDecisionProvider(config)
        assert_provider_implements_protocol(decision_provider, "decision")

    def test_assert_provider_implements_protocol_invalid(self):
        """测试assert_provider_implements_protocol无效Provider"""

        class InvalidProvider:
            pass

        invalid_provider = InvalidProvider()

        # 应该抛出断言错误
        with pytest.raises(AssertionError):
            assert_provider_implements_protocol(invalid_provider, "input")

        with pytest.raises(AssertionError):
            assert_provider_implements_protocol(invalid_provider, "decision")


# ============================================================================
# 集成测试
# ============================================================================


class TestIntegration(PluginTestBase):
    """集成测试：测试Plugin + Provider + EventBus组合使用"""

    @pytest.mark.asyncio
    async def test_plugin_with_providers_integration(self):
        """测试Plugin与Provider的集成"""

        # 创建一个简单的Plugin实现
        class SimplePlugin:
            def __init__(self, config):
                self.config = config
                self._providers = []

            async def setup(self, event_bus, config):
                decision_provider = MockDecisionProvider(config.get("decision", {}))
                await decision_provider.setup(event_bus, config)
                self._providers.append(decision_provider)
                return self._providers

            async def cleanup(self):
                for provider in self._providers:
                    await provider.cleanup()
                self._providers.clear()

            def get_info(self):
                return {
                    "name": "SimplePlugin",
                    "version": "1.0.0",
                    "author": "Test",
                    "description": "Simple test plugin",
                    "category": "processing",
                    "api_version": "1.0",
                }

        # 创建插件
        plugin = SimplePlugin({"enabled": True})

        # 验证插件信息
        info = plugin.get_info()
        assert_plugin_info_valid(info)

        # 设置插件
        providers = await plugin.setup(self.event_bus, plugin.config)

        # 验证Provider
        assert len(providers) > 0
        await self.verify_provider_lifecycle(providers)

        # 清理插件
        await self.verify_plugin_cleanup(plugin, providers)

    @pytest.mark.asyncio
    async def test_event_bus_communication(self):
        """测试EventBus通信"""
        event_bus = MockEventBus()

        # 创建Provider并监听事件
        provider = MockDecisionProvider({})

        received_events = []

        async def event_handler(event_name, data, source):
            received_events.append((event_name, data, source))

        event_bus.on("test.event", event_handler)

        # 发布事件
        await event_bus.emit("test.event", {"key": "value"}, "test_source")

        # 验证事件被接收
        assert len(received_events) == 1
        assert received_events[0] == ("test.event", {"key": "value"}, "test_source")

        # 验证EventBus记录
        assert event_bus.verify_emit_called("test.event")
        assert event_bus.verify_on_called("test.event", event_handler)
