"""
事件数据契约系统测试

测试 EventRegistry、事件模型和 EventBus 集成。
"""

import pytest
import asyncio
from src.core.events import (
    EventRegistry,
    CoreEvents,
    PluginEventPrefix,
    RawDataEvent,
    NormalizedTextEvent,
    DecisionRequestEvent,
    DecisionResponseEvent,
    IntentGeneratedEvent,
    ExpressionParametersEvent,
    SystemErrorEvent,
)
from src.core.event_bus import EventBus
from pydantic import ValidationError


class TestEventRegistry:
    """测试 EventRegistry 功能"""

    def setup_method(self):
        """每个测试前清空注册表"""
        EventRegistry.clear_all()

    def test_register_core_event(self):
        """测试注册核心事件"""
        EventRegistry.register_core_event("perception.test.event", RawDataEvent)
        assert EventRegistry.is_registered("perception.test.event")
        assert EventRegistry.is_core_event("perception.test.event")

    def test_register_core_event_invalid_prefix(self):
        """测试注册核心事件时无效前缀"""
        with pytest.raises(ValueError):
            EventRegistry.register_core_event("invalid.event", RawDataEvent)

    def test_register_plugin_event(self):
        """测试注册插件事件"""
        EventRegistry.register_plugin_event("plugin.test.my_event", NormalizedTextEvent)
        assert EventRegistry.is_registered("plugin.test.my_event")
        assert EventRegistry.is_plugin_event("plugin.test.my_event")

    def test_register_plugin_event_invalid_prefix(self):
        """测试注册插件事件时无效前缀"""
        with pytest.raises(ValueError):
            EventRegistry.register_plugin_event("invalid.event", NormalizedTextEvent)

    def test_get_event(self):
        """测试获取事件模型"""
        EventRegistry.register_core_event("perception.test", RawDataEvent)
        EventRegistry.register_plugin_event("plugin.test", NormalizedTextEvent)
        assert EventRegistry.get("perception.test") == RawDataEvent
        assert EventRegistry.get("plugin.test") == NormalizedTextEvent
        assert EventRegistry.get("unregistered.event") is None

    def test_list_events(self):
        """测试列出所有事件"""
        EventRegistry.register_core_event("perception.test1", RawDataEvent)
        EventRegistry.register_core_event("perception.test2", NormalizedTextEvent)
        EventRegistry.register_plugin_event("plugin.test", DecisionRequestEvent)

        all_events = EventRegistry.list_all_events()
        assert len(all_events) == 3
        assert "perception.test1" in all_events
        assert "perception.test2" in all_events
        assert "plugin.test" in all_events

    def test_list_plugin_events_by_plugin(self):
        """测试列出指定插件的事件"""
        EventRegistry.register_plugin_event("plugin.test.event1", DecisionRequestEvent)
        EventRegistry.register_plugin_event("plugin.test.event2", DecisionResponseEvent)
        EventRegistry.register_plugin_event("plugin.other.event", DecisionRequestEvent)

        test_events = EventRegistry.list_plugin_events_by_plugin("test")
        assert len(test_events) == 2
        assert "plugin.test.event1" in test_events
        assert "plugin.test.event2" in test_events

    def test_unregister_plugin_events(self):
        """测试注销插件事件"""
        EventRegistry.register_plugin_event("plugin.test.event1", DecisionRequestEvent)
        EventRegistry.register_plugin_event("plugin.test.event2", DecisionResponseEvent)

        count = EventRegistry.unregister_plugin_events("test")
        assert count == 2
        assert not EventRegistry.is_registered("plugin.test.event1")
        assert not EventRegistry.is_registered("plugin.test.event2")


class TestCoreEvents:
    """测试核心事件名称常量"""

    def test_core_events_constants(self):
        """测试核心事件常量"""
        assert CoreEvents.PERCEPTION_RAW_DATA_GENERATED == "perception.raw_data.generated"
        assert CoreEvents.NORMALIZATION_TEXT_READY == "normalization.text.ready"
        assert CoreEvents.DECISION_REQUEST == "decision.request"
        assert CoreEvents.DECISION_RESPONSE_GENERATED == "decision.response_generated"
        assert CoreEvents.UNDERSTANDING_INTENT_GENERATED == "understanding.intent_generated"
        assert CoreEvents.EXPRESSION_PARAMETERS_GENERATED == "expression.parameters_generated"
        assert CoreEvents.CORE_ERROR == "core.error"


class TestPluginEventPrefix:
    """测试插件事件前缀工具"""

    def test_create_plugin_event_name(self):
        """测试创建插件事件名"""
        name = PluginEventPrefix.create("my_plugin", "my_event")
        assert name == "plugin.my_plugin.my_event"


class TestEventModels:
    """测试事件 Pydantic 模型"""

    def test_raw_data_event(self):
        """测试 RawDataEvent 模型"""
        event = RawDataEvent(
            content="测试内容",
            source="test",
            data_type="text",
        )
        assert event.content == "测试内容"
        assert event.source == "test"
        assert event.data_type == "text"
        assert event.metadata == {}

    def test_normalized_text_event(self):
        """测试 NormalizedTextEvent 模型"""
        event = NormalizedTextEvent(
            text="标准化文本",
            source="bili_danmaku",
        )
        assert event.text == "标准化文本"
        assert event.source == "bili_danmaku"
        assert event.data_type == "text"

    def test_decision_request_event(self):
        """测试 DecisionRequestEvent 模型"""
        event = DecisionRequestEvent(
            canonical_message={"text": "你好"},
            context={"user_id": "123"},
        )
        assert event.canonical_message == {"text": "你好"}
        assert event.priority == 100  # 默认值

    def test_decision_response_event(self):
        """测试 DecisionResponseEvent 模型"""
        event = DecisionResponseEvent(
            response={"text": "回复内容"},
            provider="maicore",
            latency_ms=50,
        )
        assert event.response == {"text": "回复内容"}
        assert event.provider == "maicore"
        assert event.latency_ms == 50

    def test_expression_parameters_event(self):
        """测试 ExpressionParametersEvent 模型"""
        event = ExpressionParametersEvent(
            tts_text="你好呀",
            subtitle_text="你好呀",
            expressions={"happy": 0.8},
        )
        assert event.tts_text == "你好呀"
        assert event.tts_enabled is True  # 默认值
        assert event.subtitle_text == "你好呀"
        assert event.expressions == {"happy": 0.8}

    def test_system_error_event(self):
        """测试 SystemErrorEvent 模型"""
        event = SystemErrorEvent(
            error_type="ValueError",
            message="测试错误",
            source="test_module",
        )
        assert event.error_type == "ValueError"
        assert event.message == "测试错误"
        assert event.recoverable is True  # 默认值


class TestEventBusIntegration:
    """测试 EventBus 与事件系统的集成"""

    @pytest.fixture
    def event_bus(self):
        """创建 EventBus 实例"""
        bus = EventBus(enable_validation=True)
        yield bus
        asyncio.run(bus.cleanup())

    @pytest.fixture
    def register_test_events(self):
        """注册测试事件"""
        EventRegistry.register_core_event("perception.test", RawDataEvent)
        EventRegistry.register_plugin_event("plugin.test.custom_event", NormalizedTextEvent)
        yield
        EventRegistry.clear_all()

    def test_emit_valid_event(self, event_bus, register_test_events):
        """测试发布有效事件"""
        event_data = RawDataEvent(
            content="测试",
            source="test",
            data_type="text",
        )
        asyncio.run(event_bus.emit_typed("perception.test", event_data, source="test"))

    def test_emit_invalid_event_data(self, event_bus, register_test_events, caplog):
        """测试发布无效事件数据（验证失败）"""
        invalid_data = {"content": "测试"}  # 缺少必填字段 source 和 data_type
        asyncio.run(event_bus.emit("perception.test", invalid_data, source="test"))
        # 验证失败应该记录日志
        assert len(caplog.records) > 0

    def test_emit_unregistered_event(self, event_bus, register_test_events):
        """测试发布未注册事件（应该只警告，不阻断）"""
        asyncio.run(event_bus.emit("unregistered.event", {"data": "test"}, source="test"))
        # 未注册事件应该被允许

    def test_emit_dict_data_validation(self, event_bus, register_test_events):
        """测试使用 dict 数据时的验证"""
        valid_dict = {
            "content": "测试",
            "source": "test",
            "data_type": "text",
        }
        asyncio.run(event_bus.emit("perception.test", valid_dict, source="test"))
        # 有效的字典数据应该被接受

    def test_emit_without_validation(self):
        """测试不启用验证时的事件发布"""
        bus = EventBus(enable_validation=False)
        EventRegistry.register_core_event("perception.test2", RawDataEvent)

        invalid_data = {"content": "测试"}  # 缺少必填字段
        asyncio.run(bus.emit("perception.test2", invalid_data, source="test"))
        # 不启用验证时，即使数据无效也不应该报错

        asyncio.run(bus.cleanup())
        EventRegistry.clear_all()


class TestBackwardCompatibility:
    """测试向后兼容性"""

    def test_emit_without_validation_enabled(self):
        """测试未启用验证时的 emit() 调用"""
        bus = EventBus(enable_validation=False)
        asyncio.run(bus.emit("any.event.name", {"any": "data"}, source="test"))
        # 未启用验证时，任何事件都应该被接受
        asyncio.run(bus.cleanup())

    def test_emit_with_unregistered_event(self):
        """测试发布未注册的事件"""
        bus = EventBus(enable_validation=False)
        asyncio.run(bus.emit("unregistered.event", {"data": "test"}, source="test"))
        # 未注册的事件应该被接受（只警告）
        asyncio.run(bus.cleanup())
