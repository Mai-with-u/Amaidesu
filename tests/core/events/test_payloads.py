"""
事件 Payload 单元测试

测试所有事件 Payload 类的功能：
- Payload 类的创建和字段验证
- 序列化/反序列化
- from_* 工厂方法
- _debug_fields() 方法
- __str__ 方法格式化

运行: uv run pytest tests/core/events/test_payloads.py -v
"""

import pytest
import time
from typing import Dict, Any

from src.core.events.payloads.base import BasePayload
from src.core.events.payloads.input import RawDataPayload, MessageReadyPayload
from src.core.events.payloads.decision import (
    DecisionRequestPayload,
    IntentActionPayload,
    IntentPayload,
    DecisionResponsePayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
)
from src.core.events.payloads.output import (
    ParametersGeneratedPayload,
    RenderCompletedPayload,
    RenderFailedPayload,
)
from src.core.events.payloads.system import StartupPayload, ShutdownPayload, ErrorPayload
from src.domains.decision.intent import Intent, SourceContext
from src.core.types import EmotionType, ActionType, IntentAction
from src.core.base.raw_data import RawData


# =============================================================================
# BasePayload 测试
# =============================================================================


class TestBasePayload:
    """测试 BasePayload 基类"""

    def test_base_payload_creation(self):
        """测试创建 BasePayload 实例"""
        payload = BasePayload()
        assert payload is not None

    def test_debug_fields_default(self):
        """测试 _debug_fields() 默认返回所有字段"""

        class TestPayload(BasePayload):
            name: str
            value: int

        payload = TestPayload(name="test", value=42)
        fields = payload._debug_fields()
        assert "name" in fields
        assert "value" in fields

    def test_debug_fields_custom(self):
        """测试自定义 _debug_fields() 方法"""

        class TestPayload(BasePayload):
            name: str
            value: int
            secret: str

            def _debug_fields(self):
                return ["name", "value"]  # 不显示 secret

        payload = TestPayload(name="test", value=42, secret="hidden")
        fields = payload._debug_fields()
        assert "name" in fields
        assert "value" in fields
        assert "secret" not in fields

    def test_str_formatting(self):
        """测试 __str__ 方法格式化"""

        class TestPayload(BasePayload):
            name: str
            value: int

        payload = TestPayload(name="test", value=42)
        str_repr = str(payload)
        assert "TestPayload" in str_repr
        # 字符串格式化会将值用引号包裹
        assert "name=" in str_repr and "test" in str_repr
        assert "value=" in str_repr and "42" in str_repr


# =============================================================================
# RawDataPayload 测试
# =============================================================================


class TestRawDataPayload:
    """测试 RawDataPayload"""

    def test_raw_data_payload_creation(self):
        """测试创建 RawDataPayload"""
        payload = RawDataPayload(
            content="测试内容",
            source="console_input",
            data_type="text",
        )
        assert payload.content == "测试内容"
        assert payload.source == "console_input"
        assert payload.data_type == "text"

    def test_raw_data_payload_with_metadata(self):
        """测试带元数据的 RawDataPayload"""
        metadata = {"user_id": "123", "username": "测试用户"}
        payload = RawDataPayload(
            content="测试内容",
            source="bili_danmaku",
            data_type="text",
            metadata=metadata,
        )
        assert payload.metadata == metadata

    def test_raw_data_payload_timestamp(self):
        """测试时间戳自动生成"""
        before = time.time()
        payload = RawDataPayload(
            content="测试",
            source="test",
            data_type="text",
        )
        after = time.time()
        assert before <= payload.timestamp <= after

    def test_raw_data_payload_preserve_original(self):
        """测试 preserve_original 字段"""
        payload = RawDataPayload(
            content="处理后的内容",
            source="test",
            data_type="text",
            preserve_original=True,
            original_data="原始内容",
        )
        assert payload.preserve_original is True
        assert payload.original_data == "原始内容"

    def test_raw_data_payload_from_raw_data(self):
        """测试 from_raw_data 工厂方法"""
        raw_data = RawData(
            content="原始数据",
            source="console_input",
            data_type="text",
        )
        payload = RawDataPayload.from_raw_data(raw_data)
        assert payload.content == "原始数据"
        assert payload.source == "console_input"
        assert payload.data_type == "text"

    def test_raw_data_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = RawDataPayload(
            content="测试内容",
            source="console_input",
            data_type="text",
        )
        fields = payload._debug_fields()
        assert "source" in fields
        assert "data_type" in fields
        assert "content" in fields

    def test_raw_data_payload_serialization(self):
        """测试序列化"""
        payload = RawDataPayload(
            content="测试",
            source="test",
            data_type="text",
        )
        data = payload.model_dump()
        assert data["content"] == "测试"
        assert data["source"] == "test"
        assert data["data_type"] == "text"


# =============================================================================
# MessageReadyPayload 测试
# =============================================================================


class TestMessageReadyPayload:
    """测试 MessageReadyPayload"""

    def test_message_ready_payload_creation(self):
        """测试创建 MessageReadyPayload"""
        message_dict = {
            "text": "你好",
            "source": "console_input",
            "data_type": "text",
            "importance": 0.5,
            "metadata": {},
            "timestamp": time.time(),
        }
        payload = MessageReadyPayload(
            message=message_dict,
            source="console_input",
        )
        assert payload.message == message_dict
        assert payload.source == "console_input"

    def test_message_ready_payload_from_normalized_message(self):
        """测试 from_normalized_message 工厂方法"""
        from src.core.base import NormalizedMessage
        from src.domains.input.normalization.content.text_content import TextContent

        msg = NormalizedMessage(
            text="测试消息",
            source="bili_danmaku",
            data_type="text",
            content=TextContent(text="测试消息"),
            importance=0.5,
        )
        payload = MessageReadyPayload.from_normalized_message(msg)
        assert payload.message["text"] == "测试消息"
        assert payload.source == "bili_danmaku"

    def test_message_ready_payload_with_extra_metadata(self):
        """测试带额外元数据的 from_normalized_message"""
        from src.core.base import NormalizedMessage
        from src.domains.input.normalization.content.text_content import TextContent

        msg = NormalizedMessage(
            text="测试消息",
            source="test",
            data_type="text",
            content=TextContent(text="测试消息"),
            importance=0.5,
        )
        payload = MessageReadyPayload.from_normalized_message(msg, room_id="123456", extra_info="test")
        assert payload.metadata.get("room_id") == "123456"
        assert payload.metadata.get("extra_info") == "test"

    def test_message_ready_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        message_dict = {
            "text": "测试",
            "source": "test",
            "data_type": "text",
        }
        payload = MessageReadyPayload(message=message_dict, source="test")
        fields = payload._debug_fields()
        assert "source" in fields
        assert "message" in fields


# =============================================================================
# DecisionRequestPayload 测试
# =============================================================================


class TestDecisionRequestPayload:
    """测试 DecisionRequestPayload"""

    def test_decision_request_payload_creation(self):
        """测试创建 DecisionRequestPayload"""
        normalized_message = {
            "text": "你好",
            "source": "console",
            "data_type": "text",
        }
        payload = DecisionRequestPayload(
            normalized_message=normalized_message,
            priority=100,
        )
        assert payload.normalized_message == normalized_message
        assert payload.priority == 100

    def test_decision_request_payload_with_context(self):
        """测试带上下文的 DecisionRequestPayload"""
        context = {"conversation_id": "conv_456"}
        payload = DecisionRequestPayload(
            normalized_message={},
            context=context,
        )
        assert payload.context == context

    def test_decision_request_payload_priority_validation(self):
        """测试 priority 字段验证"""
        # 有效范围 0-1000
        payload = DecisionRequestPayload(
            normalized_message={},
            priority=500,
        )
        assert payload.priority == 500

    def test_decision_request_payload_priority_invalid(self):
        """测试无效的 priority 值"""
        with pytest.raises(Exception):  # ValidationError
            DecisionRequestPayload(
                normalized_message={},
                priority=1500,  # 超出范围
            )


# =============================================================================
# IntentActionPayload 测试
# =============================================================================


class TestIntentActionPayload:
    """测试 IntentActionPayload"""

    def test_intent_action_payload_creation(self):
        """测试创建 IntentActionPayload"""
        payload = IntentActionPayload(
            type="blink",
            params={"count": 2},
            priority=30,
        )
        assert payload.type == "blink"
        assert payload.params == {"count": 2}
        assert payload.priority == 30

    def test_intent_action_payload_default_params(self):
        """测试默认 params 为空字典"""
        payload = IntentActionPayload(type="wave")
        assert payload.params == {}

    def test_intent_action_payload_priority_validation(self):
        """测试 priority 字段验证 (0-100)"""
        payload = IntentActionPayload(
            type="nod",
            priority=50,
        )
        assert payload.priority == 50

    def test_intent_action_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = IntentActionPayload(
            type="blink",
            params={"count": 2},
        )
        fields = payload._debug_fields()
        assert "type" in fields
        assert "params" in fields
        assert "priority" in fields


# =============================================================================
# IntentPayload 测试
# =============================================================================


class TestIntentPayload:
    """测试 IntentPayload"""

    def test_intent_payload_creation(self):
        """测试创建 IntentPayload"""
        intent_data = {
            "original_text": "你好",
            "response_text": "你好！很高兴见到你~",
            "emotion": "happy",
            "actions": [],
            "timestamp": time.time(),
        }
        payload = IntentPayload(
            intent_data=intent_data,
            provider="maicore",
        )
        assert payload.intent_data == intent_data
        assert payload.provider == "maicore"

    def test_intent_payload_from_intent(self):
        """测试 from_intent 工厂方法"""
        intent = Intent(
            original_text="测试",
            response_text="回复",
            emotion=EmotionType.HAPPY,
            actions=[IntentAction(type=ActionType.BLINK, params={"count": 1})],
        )
        payload = IntentPayload.from_intent(intent, provider="maicore")
        assert payload.provider == "maicore"
        assert payload.intent_data["original_text"] == "测试"

    def test_intent_payload_to_intent(self):
        """测试 to_intent 方法"""
        intent_data = {
            "original_text": "测试",
            "response_text": "回复",
            "emotion": "happy",
            "actions": [],
            "timestamp": time.time(),
        }
        payload = IntentPayload(intent_data=intent_data, provider="test")
        intent = payload.to_intent()
        assert isinstance(intent, Intent)
        assert intent.original_text == "测试"
        assert intent.response_text == "回复"

    def test_intent_payload_proxy_attributes(self):
        """测试代理访问 intent_data 中的字段"""
        intent_data = {
            "original_text": "原始文本",
            "response_text": "回复文本",
            "emotion": "happy",
            "actions": [],
        }
        payload = IntentPayload(intent_data=intent_data, provider="test")
        assert payload.original_text == "原始文本"
        assert payload.response_text == "回复文本"
        assert payload.emotion == "happy"

    def test_intent_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        intent_data = {
            "original_text": "测试",
            "response_text": "回复",
            "emotion": "happy",
            "actions": [],
        }
        payload = IntentPayload(intent_data=intent_data, provider="test")
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "original_text" in fields
        assert "response_text" in fields
        assert "emotion" in fields
        assert "actions" in fields


# =============================================================================
# DecisionResponsePayload 测试
# =============================================================================


class TestDecisionResponsePayload:
    """测试 DecisionResponsePayload"""

    def test_decision_response_payload_creation(self):
        """测试创建 DecisionResponsePayload"""
        response = {
            "message_text": "你好！很高兴见到你~",
            "emotion": "happy",
        }
        payload = DecisionResponsePayload(
            response=response,
            provider="maicore",
            latency_ms=150.5,
        )
        assert payload.response == response
        assert payload.provider == "maicore"
        assert payload.latency_ms == 150.5

    def test_decision_response_payload_with_metadata(self):
        """测试带元数据的 DecisionResponsePayload"""
        metadata = {"model": "gpt-3.5-turbo"}
        payload = DecisionResponsePayload(
            response={},
            provider="test",
            metadata=metadata,
        )
        assert payload.metadata == metadata

    def test_decision_response_payload_latency_validation(self):
        """测试 latency_ms 字段验证 (>=0)"""
        payload = DecisionResponsePayload(
            response={},
            provider="test",
            latency_ms=100,
        )
        assert payload.latency_ms == 100

    def test_decision_response_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = DecisionResponsePayload(
            response={},
            provider="maicore",
            latency_ms=200,
        )
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "latency_ms" in fields


# =============================================================================
# ProviderConnectedPayload 测试
# =============================================================================


class TestProviderConnectedPayload:
    """测试 ProviderConnectedPayload"""

    def test_provider_connected_payload_creation(self):
        """测试创建 ProviderConnectedPayload"""
        payload = ProviderConnectedPayload(
            provider="maicore",
            endpoint="ws://localhost:8000/ws",
        )
        assert payload.provider == "maicore"
        assert payload.endpoint == "ws://localhost:8000/ws"

    def test_provider_connected_payload_with_metadata(self):
        """测试带元数据的 ProviderConnectedPayload"""
        metadata = {"reconnect_count": 0}
        payload = ProviderConnectedPayload(
            provider="test",
            metadata=metadata,
        )
        assert payload.metadata == metadata

    def test_provider_connected_payload_timestamp(self):
        """测试时间戳自动生成"""
        before = time.time()
        payload = ProviderConnectedPayload(provider="test")
        after = time.time()
        assert before <= payload.timestamp <= after

    def test_provider_connected_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = ProviderConnectedPayload(
            provider="maicore",
            endpoint="ws://localhost:8000/ws",
        )
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "endpoint" in fields


# =============================================================================
# ProviderDisconnectedPayload 测试
# =============================================================================


class TestProviderDisconnectedPayload:
    """测试 ProviderDisconnectedPayload"""

    def test_provider_disconnected_payload_creation(self):
        """测试创建 ProviderDisconnectedPayload"""
        payload = ProviderDisconnectedPayload(
            provider="maicore",
            reason="connection_lost",
            will_retry=True,
        )
        assert payload.provider == "maicore"
        assert payload.reason == "connection_lost"
        assert payload.will_retry is True

    def test_provider_disconnected_payload_default_reason(self):
        """测试默认 reason"""
        payload = ProviderDisconnectedPayload(provider="test")
        assert payload.reason == "unknown"

    def test_provider_disconnected_payload_with_metadata(self):
        """测试带元数据的 ProviderDisconnectedPayload"""
        metadata = {"reconnect_attempt": 1}
        payload = ProviderDisconnectedPayload(
            provider="test",
            metadata=metadata,
        )
        assert payload.metadata == metadata

    def test_provider_disconnected_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = ProviderDisconnectedPayload(
            provider="maicore",
            reason="timeout",
            will_retry=True,
        )
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "reason" in fields
        assert "will_retry" in fields


# =============================================================================
# ParametersGeneratedPayload 测试
# =============================================================================


class TestParametersGeneratedPayload:
    """测试 ParametersGeneratedPayload"""

    def test_parameters_generated_payload_creation(self):
        """测试创建 ParametersGeneratedPayload"""
        payload = ParametersGeneratedPayload(
            tts_text="你好呀~",
            subtitle_text="你好呀~",
            expressions={"happy": 0.8},
        )
        assert payload.tts_text == "你好呀~"
        assert payload.subtitle_text == "你好呀~"
        assert payload.expressions == {"happy": 0.8}

    def test_parameters_generated_payload_defaults(self):
        """测试默认字段值"""
        payload = ParametersGeneratedPayload()
        assert payload.tts_text == ""
        assert payload.tts_enabled is True
        assert payload.subtitle_enabled is True
        assert payload.expressions == {}

    def test_parameters_generated_payload_with_hotkeys(self):
        """测试带热键的 ParametersGeneratedPayload"""
        payload = ParametersGeneratedPayload(
            hotkeys=["wave", "smile"],
        )
        assert payload.hotkeys == ["wave", "smile"]

    def test_parameters_generated_payload_priority_validation(self):
        """测试 priority 字段验证 (>=0)"""
        payload = ParametersGeneratedPayload(priority=50)
        assert payload.priority == 50

    def test_parameters_generated_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = ParametersGeneratedPayload(
            tts_text="测试",
            subtitle_text="测试",
            expressions={"happy": 0.5},
        )
        fields = payload._debug_fields()
        assert "tts_text" in fields
        assert "tts_enabled" in fields
        assert "subtitle_text" in fields
        assert "subtitle_enabled" in fields
        assert "expressions" in fields


# =============================================================================
# RenderCompletedPayload 测试
# =============================================================================


class TestRenderCompletedPayload:
    """测试 RenderCompletedPayload"""

    def test_render_completed_payload_creation(self):
        """测试创建 RenderCompletedPayload"""
        payload = RenderCompletedPayload(
            provider="tts",
            output_type="audio",
            success=True,
            duration_ms=500,
        )
        assert payload.provider == "tts"
        assert payload.output_type == "audio"
        assert payload.success is True
        assert payload.duration_ms == 500

    def test_render_completed_payload_default_success(self):
        """测试默认 success=True"""
        payload = RenderCompletedPayload(
            provider="subtitle",
            output_type="text",
        )
        assert payload.success is True
        assert payload.duration_ms == 0

    def test_render_completed_payload_duration_validation(self):
        """测试 duration_ms 字段验证 (>=0)"""
        payload = RenderCompletedPayload(
            provider="test",
            output_type="test",
            duration_ms=1000,
        )
        assert payload.duration_ms == 1000

    def test_render_completed_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = RenderCompletedPayload(
            provider="vts",
            output_type="expression",
            success=True,
            duration_ms=100,
        )
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "output_type" in fields
        assert "success" in fields
        assert "duration_ms" in fields


# =============================================================================
# RenderFailedPayload 测试
# =============================================================================


class TestRenderFailedPayload:
    """测试 RenderFailedPayload"""

    def test_render_failed_payload_creation(self):
        """测试创建 RenderFailedPayload"""
        payload = RenderFailedPayload(
            provider="tts",
            output_type="audio",
            error_type="ConnectionError",
            error_message="无法连接到 TTS 服务",
        )
        assert payload.provider == "tts"
        assert payload.output_type == "audio"
        assert payload.error_type == "ConnectionError"
        assert payload.error_message == "无法连接到 TTS 服务"

    def test_render_failed_payload_recoverable(self):
        """测试 recoverable 字段"""
        payload = RenderFailedPayload(
            provider="test",
            output_type="test",
            error_type="Error",
            error_message="测试错误",
            recoverable=True,
        )
        assert payload.recoverable is True

    def test_render_failed_payload_with_metadata(self):
        """测试带元数据的 RenderFailedPayload"""
        metadata = {"retry_count": 1}
        payload = RenderFailedPayload(
            provider="test",
            output_type="test",
            error_type="Error",
            error_message="错误",
            metadata=metadata,
        )
        assert payload.metadata == metadata

    def test_render_failed_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = RenderFailedPayload(
            provider="vts",
            output_type="expression",
            error_type="TimeoutError",
            error_message="连接超时",
            recoverable=False,
        )
        fields = payload._debug_fields()
        assert "provider" in fields
        assert "output_type" in fields
        assert "error_type" in fields
        assert "error_message" in fields
        assert "recoverable" in fields


# =============================================================================
# StartupPayload 测试
# =============================================================================


class TestStartupPayload:
    """测试 StartupPayload"""

    def test_startup_payload_creation(self):
        """测试创建 StartupPayload"""
        enabled_providers = {
            "input": ["console_input", "bili_danmaku"],
            "decision": ["maicore"],
            "output": ["subtitle", "vts"],
        }
        payload = StartupPayload(
            version="0.1.0",
            config_path="/path/to/config.toml",
            debug_mode=False,
            enabled_providers=enabled_providers,
        )
        assert payload.version == "0.1.0"
        assert payload.config_path == "/path/to/config.toml"
        assert payload.debug_mode is False
        assert payload.enabled_providers == enabled_providers

    def test_startup_payload_timestamp(self):
        """测试时间戳自动生成"""
        before = time.time()
        payload = StartupPayload(version="1.0.0")
        after = time.time()
        assert before <= payload.start_time <= after


# =============================================================================
# ShutdownPayload 测试
# =============================================================================


class TestShutdownPayload:
    """测试 ShutdownPayload"""

    def test_shutdown_payload_creation(self):
        """测试创建 ShutdownPayload"""
        payload = ShutdownPayload(
            reason="user_initiated",
            uptime_seconds=3600.0,
            graceful=True,
        )
        assert payload.reason == "user_initiated"
        assert payload.uptime_seconds == 3600.0
        assert payload.graceful is True

    def test_shutdown_payload_defaults(self):
        """测试默认字段值"""
        payload = ShutdownPayload(uptime_seconds=100)
        assert payload.reason == "user_initiated"
        assert payload.graceful is True

    def test_shutdown_payload_timestamp(self):
        """测试时间戳自动生成"""
        before = time.time()
        payload = ShutdownPayload(uptime_seconds=100)
        after = time.time()
        assert before <= payload.shutdown_time <= after


# =============================================================================
# ErrorPayload 测试
# =============================================================================


class TestErrorPayload:
    """测试 ErrorPayload"""

    def test_error_payload_creation(self):
        """测试创建 ErrorPayload"""
        payload = ErrorPayload(
            error_type="ConnectionError",
            error_message="无法连接到 MaiCore 服务",
            source="MaiCoreDecisionProvider",
            recoverable=True,
        )
        assert payload.error_type == "ConnectionError"
        assert payload.error_message == "无法连接到 MaiCore 服务"
        assert payload.source == "MaiCoreDecisionProvider"
        assert payload.recoverable is True

    def test_error_payload_with_context(self):
        """测试带上下文的 ErrorPayload"""
        context = {"endpoint": "ws://localhost:8000/ws", "timeout": 30}
        payload = ErrorPayload(
            error_type="TimeoutError",
            error_message="连接超时",
            source="test",
            context=context,
        )
        assert payload.context == context

    def test_error_payload_from_exception(self):
        """测试 from_exception 工厂方法"""
        exc = ValueError("测试异常")
        payload = ErrorPayload.from_exception(
            exc,
            source="TestComponent",
            recoverable=True,
        )
        assert payload.error_type == "ValueError"
        assert payload.error_message == "测试异常"
        assert payload.source == "TestComponent"
        assert payload.recoverable is True
        assert payload.stack_trace is not None

    def test_error_payload_severity_critical(self):
        """测试不可恢复错误的严重级别"""
        exc = RuntimeError("严重错误")
        payload = ErrorPayload.from_exception(
            exc,
            source="TestComponent",
            recoverable=False,
        )
        assert payload.severity == "critical"

    def test_error_payload_debug_fields(self):
        """测试 _debug_fields() 方法"""
        payload = ErrorPayload(
            error_type="TestError",
            error_message="测试错误",
            source="test",
        )
        fields = payload._debug_fields()
        assert "error_type" in fields
        assert "error_message" in fields
        assert "source" in fields


# =============================================================================
# 运行入口
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
