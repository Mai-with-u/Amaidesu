"""
架构约束验证器测试

测试 ArchitecturalValidator 能够正确检测和阻止违反架构约束的事件订阅。
"""

import pytest

from src.core.events.architectural_validator import (
    ArchitecturalValidator,
    ArchitecturalViolationError,
)
from src.core.event_bus import EventBus
from src.core.events.names import CoreEvents


# ============================================================================
# 测试辅助类
# ============================================================================


class MockInputProvider:
    """模拟的输入 Provider（不应该订阅任何事件）"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def try_subscribe(self):
        """尝试订阅事件（应该失败）"""
        self.event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, self.handler)

    async def handler(self, event_name, data, source):
        pass


class MockOutputProvider:
    """模拟的输出 Provider（不应该订阅 Input 事件）"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def try_subscribe_to_input(self):
        """尝试订阅 Input 事件（应该失败）"""
        self.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handler)

    def try_subscribe_to_decision(self):
        """尝试订阅 Decision 事件（应该成功）"""
        self.event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, self.handler)

    async def handler(self, event_name, data, source):
        pass


class MockDecisionProvider:
    """模拟的决策 Provider（不应该订阅 Output 事件）"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def try_subscribe_to_output(self):
        """尝试订阅 Output 事件（应该失败）"""
        self.event_bus.on(CoreEvents.RENDER_COMPLETED, self.handler)

    def try_subscribe_to_input(self):
        """尝试订阅 Input 事件（应该成功）"""
        self.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handler)

    async def handler(self, event_name, data, source):
        pass


class MockFlowCoordinator:
    """模拟的 FlowCoordinator（可以订阅 Decision 事件）"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def try_subscribe(self):
        """尝试订阅 Decision 事件（应该成功）"""
        self.event_bus.on(CoreEvents.DECISION_INTENT_GENERATED, self.handler)

    async def handler(self, event_name, data, source):
        pass


# ============================================================================
# 测试用例
# ============================================================================


class TestArchitecturalValidator:
    """架构验证器测试套件"""

    def test_input_provider_cannot_subscribe_to_any_events(self):
        """测试：InputProvider 不允许订阅任何事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        provider = MockInputProvider(event_bus)

        # 尝试订阅 Decision 事件应该失败
        with pytest.raises(ArchitecturalViolationError) as exc_info:
            provider.try_subscribe()

        assert "MockInputProvider" in str(exc_info.value)
        # 事件名可能是小写形式或大写形式
        error_msg = str(exc_info.value)
        assert "decision.intent_generated" in error_msg or "DECISION_INTENT_GENERATED" in error_msg
        assert "不应该订阅任何事件" in error_msg

    def test_output_provider_cannot_subscribe_to_input_events(self):
        """测试：OutputProvider 不允许订阅 Input 事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        provider = MockOutputProvider(event_bus)

        # 尝试订阅 Input 事件应该失败
        with pytest.raises(ArchitecturalViolationError) as exc_info:
            provider.try_subscribe_to_input()

        assert "MockOutputProvider" in str(exc_info.value)
        error_msg = str(exc_info.value)
        assert "normalization.message_ready" in error_msg or "NORMALIZATION_MESSAGE_READY" in error_msg

    def test_output_provider_can_subscribe_to_decision_events(self):
        """测试：OutputProvider 可以订阅 Decision 事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        provider = MockOutputProvider(event_bus)

        # 订阅 Decision 事件应该成功
        provider.try_subscribe_to_decision()

        # 验证订阅已注册
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1

    def test_decision_provider_cannot_subscribe_to_output_events(self):
        """测试：DecisionProvider 不允许订阅 Output 事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        provider = MockDecisionProvider(event_bus)

        # 尝试订阅 Output 事件应该失败
        with pytest.raises(ArchitecturalViolationError) as exc_info:
            provider.try_subscribe_to_output()

        assert "MockDecisionProvider" in str(exc_info.value)
        error_msg = str(exc_info.value)
        assert "render.completed" in error_msg or "RENDER_COMPLETED" in error_msg

    def test_decision_provider_can_subscribe_to_input_events(self):
        """测试：DecisionProvider 可以订阅 Input 事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        provider = MockDecisionProvider(event_bus)

        # 订阅 Input 事件应该成功
        provider.try_subscribe_to_input()

        # 验证订阅已注册
        assert event_bus.get_listeners_count(CoreEvents.NORMALIZATION_MESSAGE_READY) == 1

    def test_flow_coordinator_can_subscribe_to_decision_events(self):
        """测试：FlowCoordinator 可以订阅 Decision 事件"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        coordinator = MockFlowCoordinator(event_bus)

        # 订阅 Decision 事件应该成功
        coordinator.try_subscribe()

        # 验证订阅已注册
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1

    def test_validator_can_be_disabled(self):
        """测试：验证器可以被禁用"""
        event_bus = EventBus()
        validator = ArchitecturalValidator(event_bus, enabled=True)

        # 禁用验证器
        validator.disable()

        provider = MockInputProvider(event_bus)

        # 禁用后，违规订阅应该成功
        provider.try_subscribe()
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1

    def test_validator_can_be_re_enabled(self):
        """测试：验证器可以被重新启用"""
        event_bus = EventBus()
        validator = ArchitecturalValidator(event_bus, enabled=True)

        # 禁用验证器
        validator.disable()

        # 重新启用
        validator.enable()

        provider = MockInputProvider(event_bus)

        # 重新启用后，违规订阅应该失败
        with pytest.raises(ArchitecturalViolationError):
            provider.try_subscribe()

    def test_disabled_validator_does_not_wrap_methods(self):
        """测试：未启用的验证器不包装方法"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=False)

        provider = MockInputProvider(event_bus)

        # 验证器未启用，违规订阅应该成功
        provider.try_subscribe()
        assert event_bus.get_listeners_count(CoreEvents.DECISION_INTENT_GENERATED) == 1

    def test_wildcard_event_patterns(self):
        """测试：通配符事件模式匹配"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True)

        # 创建一个允许订阅 expression.* 的类
        class MockProviderWithWildcard:
            def __init__(self, event_bus: EventBus):
                self.event_bus = event_bus

            def try_subscribe(self):
                # 应该成功（匹配 expression.* 通配符）
                self.event_bus.on("expression.parameters_generated", self.handler)

            async def handler(self, event_name, data, source):
                pass

        provider = MockProviderWithWildcard(event_bus)

        # 订阅应该成功
        provider.try_subscribe()
        assert event_bus.get_listeners_count("expression.parameters_generated") == 1

    def test_strict_mode_blocks_unknown_subscribers(self):
        """测试：严格模式阻止未知订阅者"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True, strict=True)

        # 创建一个未配置的类
        class UnknownSubscriber:
            def __init__(self, event_bus: EventBus):
                self.event_bus = event_bus

            def try_subscribe(self):
                self.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handler)

            async def handler(self, event_name, data, source):
                pass

        subscriber = UnknownSubscriber(event_bus)

        # 严格模式下，未知订阅者应该被阻止
        with pytest.raises(ArchitecturalViolationError):
            subscriber.try_subscribe()

    def test_non_strict_mode_allows_unknown_subscribers(self):
        """测试：非严格模式允许未知订阅者（向后兼容）"""
        event_bus = EventBus()
        ArchitecturalValidator(event_bus, enabled=True, strict=False)

        # 创建一个未配置的类
        class UnknownSubscriber:
            def __init__(self, event_bus: EventBus):
                self.event_bus = event_bus

            def try_subscribe(self):
                self.event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY, self.handler)

            async def handler(self, event_name, data, source):
                pass

        subscriber = UnknownSubscriber(event_bus)

        # 非严格模式下，未知订阅者应该被允许
        subscriber.try_subscribe()
        assert event_bus.get_listeners_count(CoreEvents.NORMALIZATION_MESSAGE_READY) == 1

    def test_architectural_validator_config_complete(self):
        """验证：架构验证器配置包含所有已注册的 Provider"""
        from src.core.events.architectural_validator import ArchitecturalValidator

        # 创建验证器实例
        validator = ArchitecturalValidator(EventBus(), enabled=False)

        # 测试所有已知的 Provider 类都能通过 _get_base_classes 找到基类
        # 这确保 inheritance_map 配置是完整的
        known_providers = [
            # Input Providers
            "ConsoleInputProvider",
            "BiliDanmakuInputProvider",
            "BiliDanmakuOfficialInputProvider",
            "BiliDanmakuOfficialMaiCraftInputProvider",
            "MainosabaInputProvider",
            "ReadPingmuInputProvider",
            "MockDanmakuInputProvider",
            # Decision Providers
            "MaiCoreDecisionProvider",
            "LocalLLMDecisionProvider",
            "RuleEngineDecisionProvider",
            "MockDecisionProvider",
            # Output Providers
            "VTSProvider",
            "EdgeTTSProvider",
            "SubtitleOutputProvider",
            "AvatarOutputProvider",
            "GPTSoVITSOutputProvider",
            "OmniTTSProvider",
            "StickerOutputProvider",
            "RemoteStreamOutputProvider",
            "WarudoOutputProvider",
            "ObsControlOutputProvider",
            "MockOutputProvider",
        ]

        for provider_class in known_providers:
            base_classes = validator._get_base_classes(provider_class)
            # 每个 Provider 应该至少有一个基类
            assert len(base_classes) > 0, f"{provider_class} 没有在 inheritance_map 中配置基类"

        # 验证核心基类能够被识别
        assert validator._is_provider_base_class("InputProvider")
        assert validator._is_provider_base_class("DecisionProvider")
        assert validator._is_provider_base_class("OutputProvider")
