"""
插件系统测试工具

提供插件和Provider测试的辅助工具:
- Plugin测试基类: 简化Plugin测试的fixture和setup
- Provider测试辅助函数: 简化Provider测试
- EventBus mock工具: 简化EventBus测试
"""

import pytest
import asyncio
from typing import Any, Dict, List, AsyncIterator
from unittest.mock import Mock

from src.core.event_bus import EventBus
from src.core.plugin import Plugin
from src.core.providers.input_provider import InputProvider
from src.core.providers.decision_provider import DecisionProvider
from src.core.data_types.raw_data import RawData
from maim_message import MessageBase

if False:  # TYPE_CHECKING
    from src.canonical.canonical_message import CanonicalMessage


# ============================================================================
# Plugin测试基类
# ============================================================================


class MockProvider:
    """Mock Provider用于测试"""

    def __init__(self, provider_type: str = "mock"):
        self.provider_type = provider_type
        self.setup_called = False
        self.cleanup_called = False
        self.config = {}

    async def setup(self, event_bus: EventBus, config: dict):
        """Mock setup方法"""
        self.setup_called = True
        self.config = config

    async def cleanup(self):
        """Mock cleanup方法"""
        self.cleanup_called = True


class MockInputProvider(InputProvider):
    """Mock InputProvider用于测试"""

    def __init__(self, config: dict, test_data: List[Any] = None):
        super().__init__(config)
        self.test_data = test_data or []
        self.start_called = False
        self.stop_called = False

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """产生测试数据"""
        self.start_called = True
        for data in self.test_data:
            yield data


class MockDecisionProvider(DecisionProvider):
    """Mock DecisionProvider用于测试"""

    def __init__(self, config: dict, event_bus=None):
        super().__init__(config, event_bus)
        self.setup_internal_called = False
        self.cleanup_internal_called = False
        self.decide_calls = []

    async def _setup_internal(self):
        """Mock内部设置"""
        self.setup_internal_called = True

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """Mock决策方法"""
        self.decide_calls.append(canonical_message)
        # 返回一个mock MessageBase
        mock_msg = Mock(spec=MessageBase)
        return mock_msg

    async def _cleanup_internal(self):
        """Mock内部清理"""
        self.cleanup_internal_called = True


class PluginTestBase:
    """
    Plugin测试基类

    提供通用的fixture和测试方法，简化Plugin测试。

    使用方法:
    ```python
    class TestMyPlugin(PluginTestBase):
        @pytest.mark.asyncio
        async def test_plugin_setup(self, plugin_factory):
            plugin = plugin_factory(MyPlugin, {"enabled": True})
            providers = await plugin.setup(self.event_bus, plugin.config)

            assert len(providers) > 0
            assert all(isinstance(p, InputProvider) for p in providers)
    ```
    """

    @pytest.fixture
    def event_bus(self):
        """提供EventBus fixture"""
        return EventBus(enable_stats=False)

    @pytest.fixture
    def plugin_config(self):
        """提供默认plugin配置"""
        return {
            "name": "test_plugin",
            "version": "1.0.0",
            "enabled": True,
            "config": {},
        }

    @pytest.fixture
    def plugin_factory(self, event_bus):
        """
        Plugin工厂函数

        创建Plugin实例并配置基本属性。

        Args:
            event_bus: EventBus实例

        Returns:
            Plugin实例
        """

        def factory(plugin_class, config: Dict[str, Any] = None) -> Plugin:
            """创建Plugin实例"""
            if config is None:
                config = {}

            # 确保有基本配置
            final_config = {"name": "test_plugin", "version": "1.0.0", **config}

            plugin = plugin_class(final_config)
            plugin._test_event_bus = event_bus
            return plugin

        return factory

    @pytest.mark.asyncio
    async def verify_plugin_info(self, plugin: Plugin):
        """
        验证Plugin信息

        Args:
            plugin: Plugin实例
        """
        info = plugin.get_info()

        assert "name" in info
        assert "version" in info
        assert "author" in info
        assert "description" in info
        assert "category" in info
        assert "api_version" in info

        assert isinstance(info["name"], str)
        assert isinstance(info["version"], str)
        assert isinstance(info["category"], str)
        assert info["category"] in ["input", "output", "processing", "game", "hardware", "software"]

    @pytest.mark.asyncio
    async def verify_provider_lifecycle(self, providers: List[Any]):
        """
        验证Provider生命周期

        Args:
            providers: Provider列表
        """
        for provider in providers:
            # 验证Provider有setup和cleanup方法
            assert hasattr(provider, "setup")
            assert hasattr(provider, "cleanup")
            assert callable(provider.setup)
            assert callable(provider.cleanup)

            # 验证setup被调用过
            if isinstance(provider, MockProvider):
                assert provider.setup_called, f"{provider} setup未调用"

    @pytest.mark.asyncio
    async def verify_plugin_cleanup(self, plugin: Plugin, providers: List[Any]):
        """
        验证Plugin清理

        Args:
            plugin: Plugin实例
            providers: Provider列表
        """
        await plugin.cleanup()

        for provider in providers:
            if isinstance(provider, MockProvider):
                assert provider.cleanup_called, f"{provider} cleanup未调用"


# ============================================================================
# Provider测试辅助函数
# ============================================================================


class ProviderTestHelper:
    """
    Provider测试辅助函数

    提供通用的Provider测试方法。
    """

    @staticmethod
    async def verify_input_provider_lifecycle(provider: InputProvider, test_data: List[Any]):
        """
        验证InputProvider生命周期

        Args:
            provider: InputProvider实例
            test_data: 测试数据列表
        """
        assert not provider.is_running

        # 启动provider
        collected_data = []
        async for data in provider.start():
            collected_data.append(data)

        # 验证数据
        assert len(collected_data) == len(test_data)
        assert not provider.is_running

        # 停止provider
        await provider.stop()

    @staticmethod
    async def verify_decision_provider_lifecycle(provider: DecisionProvider, event_bus: EventBus):
        """
        验证DecisionProvider生命周期

        Args:
            provider: DecisionProvider实例
            event_bus: EventBus实例
        """
        assert not provider.is_setup

        # 设置provider
        config = {"test": "config"}
        await provider.setup(event_bus, config)

        assert provider.is_setup
        assert provider.event_bus == event_bus

        # 清理provider
        await provider.cleanup()

        assert not provider.is_setup

    @staticmethod
    def create_mock_message_base(text: str = "test") -> MessageBase:
        """
        创建mock MessageBase

        Args:
            text: 消息文本

        Returns:
            MessageBase mock对象
        """
        mock_msg = Mock(spec=MessageBase)
        mock_msg.message_segment = Mock()
        mock_msg.message_segment.data = text
        mock_msg.message_segment.type = "text"
        return mock_msg

    @staticmethod
    def create_raw_data(content: Any, source: str = "test", data_type: str = "text") -> RawData:
        """
        创建RawData

        Args:
            content: 数据内容
            source: 数据源
            data_type: 数据类型

        Returns:
            RawData实例
        """
        return RawData(content=content, source=source, data_type=data_type, timestamp=1234567890)


# ============================================================================
# EventBus Mock工具
# ============================================================================


class MockEventBus:
    """
    Mock EventBus

    用于测试时的轻量级EventBus替代品。

    特点:
    - 不依赖真实EventBus的实现
    - 记录所有emit和on操作
    - 同步执行处理器(简化测试)
    - 支持基本的发布/订阅功能
    """

    def __init__(self):
        """初始化Mock EventBus"""
        self._handlers: Dict[str, List[callable]] = {}
        self._emits: List[Dict[str, Any]] = []
        self._ons: List[Dict[str, Any]] = []

    async def emit(self, event_name: str, data: Any, source: str = "unknown"):
        """
        发布事件(同步版本)

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件源
        """
        self._emits.append({"event": event_name, "data": data, "source": source})

        # 同步执行所有处理器
        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                await handler(event_name, data, source)
            else:
                handler(event_name, data, source)

    def on(self, event_name: str, handler: callable, priority: int = 100):
        """
        订阅事件

        Args:
            event_name: 事件名称
            handler: 处理器函数
            priority: 优先级(忽略，用于兼容真实EventBus)
        """
        if event_name not in self._handlers:
            self._handlers[event_name] = []

        self._handlers[event_name].append(handler)
        self._ons.append({"event": event_name, "handler": handler, "priority": priority})

    def off(self, event_name: str, handler: callable):
        """
        取消订阅

        Args:
            event_name: 事件名称
            handler: 处理器函数
        """
        if event_name in self._handlers:
            handlers = self._handlers[event_name]
            if handler in handlers:
                handlers.remove(handler)

    def get_listeners_count(self, event_name: str) -> int:
        """
        获取事件监听器数量

        Args:
            event_name: 事件名称

        Returns:
            监听器数量
        """
        return len(self._handlers.get(event_name, []))

    def get_emits(self) -> List[Dict[str, Any]]:
        """
        获取所有emit记录

        Returns:
            emit记录列表
        """
        return self._emits.copy()

    def get_ons(self) -> List[Dict[str, Any]]:
        """
        获取所有on记录

        Returns:
            on记录列表
        """
        return self._ons.copy()

    def clear(self):
        """清除所有记录"""
        self._handlers.clear()
        self._emits.clear()
        self._ons.clear()

    def verify_emit_called(self, event_name: str, data: Any = None, source: str = None) -> bool:
        """
        验证指定事件是否被emit过

        Args:
            event_name: 事件名称
            data: 事件数据(可选)
            source: 事件源(可选)

        Returns:
            是否emit过
        """
        for emit in self._emits:
            if emit["event"] != event_name:
                continue

            if data is not None and emit["data"] != data:
                continue

            if source is not None and emit["source"] != source:
                continue

            return True

        return False

    def verify_on_called(self, event_name: str, handler: callable) -> bool:
        """
        验证指定处理器是否被on过

        Args:
            event_name: 事件名称
            handler: 处理器函数

        Returns:
            是否on过
        """
        for on in self._ons:
            if on["event"] == event_name and on["handler"] == handler:
                return True

        return False


# ============================================================================
# 测试Fixture工厂
# ============================================================================


@pytest.fixture
def mock_event_bus():
    """提供Mock EventBus fixture"""
    return MockEventBus()


@pytest.fixture
def real_event_bus():
    """提供真实EventBus fixture"""
    return EventBus(enable_stats=False)


@pytest.fixture
def provider_test_helper():
    """提供ProviderTestHelper fixture"""
    return ProviderTestHelper()


# ============================================================================
# 辅助断言函数
# ============================================================================


def assert_plugin_info_valid(info: Dict[str, Any]):
    """
    断言Plugin信息有效

    Args:
        info: Plugin信息字典
    """
    assert "name" in info, "Plugin信息缺少name字段"
    assert "version" in info, "Plugin信息缺少version字段"
    assert "author" in info, "Plugin信息缺少author字段"
    assert "description" in info, "Plugin信息缺少description字段"
    assert "category" in info, "Plugin信息缺少category字段"
    assert "api_version" in info, "Plugin信息缺少api_version字段"

    assert isinstance(info["name"], str), "name必须是字符串"
    assert isinstance(info["version"], str), "version必须是字符串"
    assert isinstance(info["category"], str), "category必须是字符串"

    valid_categories = ["input", "output", "processing", "game", "hardware", "software"]
    assert info["category"] in valid_categories, f"category必须是{valid_categories}之一"


def assert_provider_implements_protocol(provider: Any, protocol_type: str):
    """
    断言Provider实现了指定协议

    Args:
        provider: Provider实例
        protocol_type: 协议类型("input", "output", "decision")
    """
    if protocol_type == "input":
        assert isinstance(provider, InputProvider), "Provider必须继承InputProvider"
        assert hasattr(provider, "start"), "InputProvider必须有start方法"
        assert hasattr(provider, "stop"), "InputProvider必须有stop方法"
    elif protocol_type == "decision":
        assert isinstance(provider, DecisionProvider), "Provider必须继承DecisionProvider"
        assert hasattr(provider, "decide"), "DecisionProvider必须有decide方法"
        assert hasattr(provider, "setup"), "DecisionProvider必须有setup方法"
    else:
        raise ValueError(f"未知的协议类型: {protocol_type}")
