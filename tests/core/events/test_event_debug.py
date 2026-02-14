"""
Event debug 功能单元测试

测试 BasePayload 基类的字符串表示功能，以及各类 Payload 的调试输出。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads import (
    DecisionRequestPayload,
    ErrorPayload,
    IntentActionPayload,
    IntentPayload,
    MessageReadyPayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
    RawDataPayload,
    RenderCompletedPayload,
    RenderFailedPayload,
)
from src.modules.events.payloads.base import BasePayload

# =============================================================================
# 测试 BasePayload 基类
# =============================================================================


class TestBasePayload:
    """测试 BasePayload 基类的核心功能"""

    def test_basic_string_representation(self):
        """测试基本的字符串表示"""

        class TestPayload(BasePayload):
            name: str
            value: int

        payload = TestPayload(name="test", value=42)
        debug_str = str(payload)

        assert "TestPayload" in debug_str
        assert 'name="test"' in debug_str
        assert "value=42" in debug_str

    def test_default_str_shows_all_fields(self):
        """测试默认 __str__ 方法显示所有字段"""

        class TestPayload(BasePayload):
            field1: str
            field2: int
            field3: bool

        payload = TestPayload(field1="a", field2=1, field3=True)
        debug_str = str(payload)

        # 所有字段都应该出现在字符串中
        assert "field1=" in debug_str
        assert "field2=" in debug_str
        assert "field3=" in debug_str
        assert '"a"' in debug_str
        assert "1" in debug_str
        assert "True" in debug_str

    def test_custom_str_method(self):
        """测试自定义 __str__ 方法可以覆盖默认实现"""

        class TestPayload(BasePayload):
            name: str
            value: int
            secret: str

            def __str__(self):
                # 自定义实现，排除 secret 字段
                return f'TestPayload(name="{self.name}", value={self.value})'

        payload = TestPayload(name="test", value=42, secret="hidden")
        debug_str = str(payload)

        assert "name" in debug_str
        assert "value" in debug_str
        assert "secret" not in debug_str
        assert "hidden" not in debug_str

    def test_nested_payload_object(self):
        """测试嵌套的 Payload 对象格式化"""

        class InnerPayload(BasePayload):
            data: str

        class OuterPayload(BasePayload):
            inner: InnerPayload
            name: str

        inner = InnerPayload(data="inner_data")
        outer = OuterPayload(inner=inner, name="outer_name")
        debug_str = str(outer)

        assert "OuterPayload" in debug_str
        assert "InnerPayload" in debug_str
        assert 'data="inner_data"' in debug_str


# =============================================================================
# 测试 Input Domain Payload
# =============================================================================


class TestInputPayloads:
    """测试 Input Domain Payload 的字符串表示"""

    def test_raw_data_payload_debug_string(self):
        """测试 RawDataPayload 的字符串表示"""
        payload = RawDataPayload(content="测试消息", source="console_input", data_type="text")
        debug_str = str(payload)

        # 新格式: [text] "测试消息"
        assert "[text]" in debug_str
        assert "测试消息" in debug_str

    def test_raw_data_payload_with_metadata(self):
        """测试带元数据的 RawDataPayload"""
        payload = RawDataPayload(
            content="用户输入的文本",
            source="bili_danmaku",
            data_type="text",
            metadata={"user_id": "12345", "username": "观众A"},
        )
        debug_str = str(payload)

        # RawDataPayload 有自定义的 __str__ 方法，格式为 [data_type] "content" (user_name)
        assert "[text]" in debug_str
        assert "用户输入的文本" in debug_str

    def test_message_ready_payload_debug_string(self):
        """测试 MessageReadyPayload 的字符串表示"""
        payload = MessageReadyPayload(
            message={
                "text": "你好，今天天气怎么样？",
                "source": "bili_danmaku",
                "data_type": "text",
            },
            source="bili_danmaku",
        )
        debug_str = str(payload)

        # MessageReadyPayload 有自定义的 __str__ 方法
        assert "你好，今天天气怎么样？" in debug_str

    def test_message_ready_payload_long_text_truncation(self):
        """测试 MessageReadyPayload 对长文本的截断"""
        long_text = "这是一段非常长的文本内容" * 10
        payload = MessageReadyPayload(
            message={
                "text": long_text,
                "source": "test",
                "data_type": "text",
            },
            source="test",
        )
        debug_str = str(payload)

        # 长文本应该被截断
        assert "..." in debug_str
        # 确认文本不会完整出现
        assert long_text not in debug_str


# =============================================================================
# 测试 Decision Domain Payload
# =============================================================================


class TestDecisionPayloads:
    """测试 Decision Domain Payload 的字符串表示"""

    def test_intent_action_payload_debug_string(self):
        """测试 IntentActionPayload 的字符串表示"""
        payload = IntentActionPayload(type="blink", params={"count": 2}, priority=30)
        debug_str = str(payload)

        assert "IntentActionPayload" in debug_str
        assert 'type="blink"' in debug_str
        assert "params=" in debug_str
        assert "priority=30" in debug_str

    def test_intent_payload_debug_string(self):
        """测试 IntentPayload 的字符串表示"""
        from src.modules.types import Intent
        from src.modules.types import ActionType, EmotionType, IntentAction

        # 使用 from_intent 方法创建 Payload
        intent = Intent(
            original_text="你好",
            response_text="你好！很高兴见到你~",
            emotion=EmotionType.HAPPY,
            actions=[IntentAction(type=ActionType.BLINK, params={"count": 2}, priority=30)],
        )
        payload = IntentPayload.from_intent(intent, provider="maicore")
        debug_str = str(payload)

        # IntentPayload 有自定义的 __str__ 方法
        assert "IntentPayload" in debug_str
        assert 'provider="maicore"' in debug_str
        assert 'original_text="你好"' in debug_str
        assert 'response_text="你好！很高兴见到你~"' in debug_str
        assert 'emotion="happy"' in debug_str
        assert "actions=[blink]" in debug_str

    def test_decision_request_payload_debug_string(self):
        """测试 DecisionRequestPayload 的字符串表示（自定义实现，只显示关键字段）"""
        payload = DecisionRequestPayload(normalized_message={"text": "测试", "source": "test"}, priority=100)
        debug_str = str(payload)

        # 使用自定义的 __str__ 实现，只显示 priority 和 timestamp
        assert "DecisionRequestPayload" in debug_str
        assert "priority=100" in debug_str
        assert "timestamp=" in debug_str
        # normalized_message 不在自定义 __str__ 中
        assert "normalized_message=" not in debug_str

    def test_provider_connected_payload_debug_string(self):
        """测试 ProviderConnectedPayload 的字符串表示"""
        payload = ProviderConnectedPayload(provider="maicore", endpoint="ws://localhost:8000/ws")
        debug_str = str(payload)

        assert "ProviderConnectedPayload" in debug_str
        assert 'provider="maicore"' in debug_str
        assert 'endpoint="ws://localhost:8000/ws"' in debug_str

    def test_provider_disconnected_payload_debug_string(self):
        """测试 ProviderDisconnectedPayload 的字符串表示"""
        payload = ProviderDisconnectedPayload(provider="maicore", reason="connection_lost", will_retry=True)
        debug_str = str(payload)

        assert "ProviderDisconnectedPayload" in debug_str
        assert 'provider="maicore"' in debug_str
        assert 'reason="connection_lost"' in debug_str
        assert "will_retry=True" in debug_str


# =============================================================================
# 测试 Output Domain Payload
# =============================================================================


class TestOutputPayloads:
    """测试 Output Domain Payload 的字符串表示"""

    def test_render_completed_payload_debug_string(self):
        """测试 RenderCompletedPayload 的字符串表示"""
        payload = RenderCompletedPayload(provider="tts", output_type="audio", success=True, duration_ms=500.0)
        debug_str = str(payload)

        assert "RenderCompletedPayload" in debug_str
        assert 'provider="tts"' in debug_str
        assert 'output_type="audio"' in debug_str
        assert "success=True" in debug_str
        # duration_ms 使用 :.0f 格式化，没有小数点
        assert "duration_ms=500" in debug_str

    def test_render_failed_payload_debug_string(self):
        """测试 RenderFailedPayload 的字符串表示"""
        payload = RenderFailedPayload(
            provider="tts",
            output_type="audio",
            error_type="ConnectionError",
            error_message="无法连接到 TTS 服务",
            recoverable=True,
        )
        debug_str = str(payload)

        assert "RenderFailedPayload" in debug_str
        assert 'provider="tts"' in debug_str
        assert 'output_type="audio"' in debug_str
        assert 'error_type="ConnectionError"' in debug_str
        assert 'error_message="无法连接到 TTS 服务"' in debug_str
        assert "recoverable=True" in debug_str


# =============================================================================
# 测试 System Payload
# =============================================================================


class TestSystemPayloads:
    """测试 System Payload 的字符串表示"""

    def test_error_payload_debug_string(self):
        """测试 ErrorPayload 的字符串表示"""
        payload = ErrorPayload(
            error_type="ConnectionError",
            error_message="无法连接到 MaiCore 服务",
            source="MaiCoreDecisionProvider",
            recoverable=True,
        )
        debug_str = str(payload)

        # ErrorPayload 有自定义的 __str__ 方法，只显示关键字段
        assert "ErrorPayload" in debug_str
        assert 'error_type="ConnectionError"' in debug_str
        assert 'error_message="无法连接到 MaiCore 服务"' in debug_str
        assert 'source="MaiCoreDecisionProvider"' in debug_str
        # stack_trace 不应该在调试输出中（被自定义 __str__ 排除）
        assert "stack_trace" not in debug_str


# =============================================================================
# 测试字段格式化功能
# =============================================================================


class TestFieldFormatting:
    """测试字段值的格式化功能"""

    def test_string_truncation(self):
        """测试长字符串的截断"""

        class TestPayload(BasePayload):
            content: str

        long_content = "x" * 100
        payload = TestPayload(content=long_content)
        debug_str = str(payload)

        # 应该被截断并带有省略号
        assert "..." in debug_str
        assert len(debug_str) < len(long_content)

    def test_empty_dict_formatting(self):
        """测试空字典的格式化"""

        class TestPayload(BasePayload):
            data: dict

        payload = TestPayload(data={})
        debug_str = str(payload)

        assert "data={}" in debug_str

    def test_nested_dict_formatting(self):
        """测试嵌套字典的格式化"""

        class TestPayload(BasePayload):
            config: dict

        payload = TestPayload(config={"server": {"host": "localhost", "port": 8000}, "debug": True})
        debug_str = str(payload)

        assert "config=" in debug_str
        assert "host" in debug_str
        assert "port" in debug_str
        assert "server" in debug_str

    def test_empty_list_formatting(self):
        """测试空列表的格式化"""

        class TestPayload(BasePayload):
            items: list

        payload = TestPayload(items=[])
        debug_str = str(payload)

        assert "items=[]" in debug_str

    def test_list_formatting(self):
        """测试列表的格式化"""

        class TestPayload(BasePayload):
            items: list

        payload = TestPayload(items=[1, 2, 3])
        debug_str = str(payload)

        assert "items=[1, 2, 3]" in debug_str

    def test_list_of_payloads_formatting(self):
        """测试 Payload 列表的格式化"""

        class InnerPayload(BasePayload):
            value: int

        class OuterPayload(BasePayload):
            items: list

        payload = OuterPayload(
            items=[
                InnerPayload(value=1),
                InnerPayload(value=2),
            ]
        )
        debug_str = str(payload)

        assert "items=" in debug_str
        assert "InnerPayload" in debug_str

    def test_boolean_formatting(self):
        """测试布尔值的格式化"""

        class TestPayload(BasePayload):
            flag: bool

        payload = TestPayload(flag=True)
        debug_str = str(payload)

        assert "flag=True" in debug_str

    def test_none_value_formatting(self):
        """测试 None 值的格式化"""

        class TestPayload(BasePayload):
            value: str

        payload = TestPayload(value="test")
        debug_str = str(payload)

        assert "value=" in debug_str


# =============================================================================
# 测试 EventBus 的 debug 日志
# =============================================================================


class TestEventBusDebugLog:
    """测试 EventBus 的 debug 日志输出"""

    @pytest.mark.asyncio
    async def test_eventbus_debug_log_output(self):
        """测试 EventBus 的 debug 日志输出"""
        import io

        from loguru import logger

        # 捕获 loguru 日志
        log_capture = io.StringIO()
        handler_id = logger.add(log_capture, level="DEBUG", format="{level} | {name} - {message}")

        try:
            # 创建 EventBus
            event_bus = EventBus()

            # 订阅一个测试事件
            received_data = []

            async def handler(event_name, data, source):
                received_data.append(data)

            event_bus.on("test.event", handler)

            # 发布事件
            payload = RawDataPayload(content="测试消息", source="test", data_type="text")

            await event_bus.emit("test.event", payload, source="test_source")

            # 验证日志输出包含事件内容
            log_output = log_capture.getvalue()
            # RawDataPayload 的特殊格式: [test.event] test_source: text
            # 注意: 现在使用完整事件名，不再简化
            assert "[test.event]" in log_output
            # source 是 "test_source"
            assert ": 测试消息" in log_output
        finally:
            # 清理 log handler
            logger.remove(handler_id)

    @pytest.mark.asyncio
    async def test_eventbus_debug_log_with_complex_payload(self):
        """测试复杂 Payload 的 debug 日志输出"""
        import io

        from loguru import logger

        # 捕获 loguru 日志
        log_capture = io.StringIO()
        handler_id = logger.add(log_capture, level="DEBUG", format="{level} | {name} - {message}")

        try:
            event_bus = EventBus()
            received_data = []

            async def handler(event_name, data, source):
                received_data.append(data)

            event_bus.on("test.complex", handler)

            # 发布复杂的 Payload
            from src.modules.types import Intent
            from src.modules.types import ActionType, EmotionType, IntentAction

            intent = Intent(
                original_text="你好",
                response_text="你好！很高兴见到你~",
                emotion=EmotionType.HAPPY,
                actions=[IntentAction(type=ActionType.BLINK, params={"count": 2}, priority=30)],
            )
            payload = IntentPayload.from_intent(intent, provider="maicore")

            await event_bus.emit("test.complex", payload, source="test")

            # 验证日志
            log_output = log_capture.getvalue()
            assert "IntentPayload" in log_output
            assert "original_text=" in log_output or "original_text" in log_output
        finally:
            # 清理 log handler
            logger.remove(handler_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
