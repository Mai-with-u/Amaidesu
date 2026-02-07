"""
测试 ProviderRegistry（pytest）

运行: uv run pytest tests/domains/rendering/test_provider_registry.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from typing import AsyncIterator

from src.core.provider_registry import ProviderRegistry
from src.core.base.input_provider import InputProvider
from src.core.base.output_provider import OutputProvider
from src.core.base.decision_provider import DecisionProvider
from src.core.base.raw_data import RawData
from src.core.base.base import RenderParameters


# =============================================================================
# Mock Provider 类（用于测试）
# =============================================================================


class MockInputProvider(InputProvider):
    """Mock InputProvider for testing"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.test_value = config.get("test_value", "default")

    async def _collect_data(self) -> AsyncIterator[RawData]:
        # 简单的实现，不需要真实数据
        return
        yield  # noqa: B901  # 使方法成为异步生成器


class MockOutputProvider(OutputProvider):
    """Mock OutputProvider for testing"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.test_value = config.get("test_value", "default")

    async def _render_internal(self, parameters: RenderParameters):
        # 简单的实现
        pass


class MockDecisionProvider(DecisionProvider):
    """Mock DecisionProvider for testing"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.test_value = config.get("test_value", "default")

    async def decide(self, message):
        # 简单的实现
        from src.domains.decision.intent import Intent

        return Intent(text="Test response", emotions=[])


class FailingMockProvider(InputProvider):
    """初始化时会抛出异常的 Mock Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        raise ValueError("Initialization failed")

    async def _collect_data(self) -> AsyncIterator[RawData]:
        return
        yield  # noqa: B901


# =============================================================================
# InputProvider 管理测试
# =============================================================================


def test_register_input_provider():
    """测试注册 InputProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input", MockInputProvider, source="test")

    assert ProviderRegistry.is_input_provider_registered("mock_input")
    assert "mock_input" in ProviderRegistry.get_registered_input_providers()


def test_register_input_provider_duplicate():
    """测试重复注册 InputProvider（应覆盖）"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input", MockInputProvider, source="test_source_1")

    # 重复注册（应该覆盖，不抛出异常）
    ProviderRegistry.register_input("mock_input", MockInputProvider, source="test_source_2")

    # 验证注册成功
    assert ProviderRegistry.is_input_provider_registered("mock_input")


def test_register_input_provider_invalid_type():
    """测试注册非 InputProvider 子类（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    class NotAProvider:
        pass

    with pytest.raises(TypeError):
        ProviderRegistry.register_input("invalid", NotAProvider)


def test_register_input_provider_not_class():
    """测试注册非类对象（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(TypeError):
        ProviderRegistry.register_input("invalid", "not_a_class")


def test_create_input_provider():
    """测试创建 InputProvider 实例"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input", MockInputProvider)

    config = {"test_value": "test_config"}
    provider = ProviderRegistry.create_input("mock_input", config)

    assert isinstance(provider, MockInputProvider)
    assert provider.test_value == "test_config"
    assert provider.config == config


def test_create_input_provider_not_registered():
    """测试创建未注册的 InputProvider（应抛出 ValueError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(ValueError, match="Unknown input provider"):
        ProviderRegistry.create_input("nonexistent", {})


def test_unregister_input_provider():
    """测试注销 InputProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input", MockInputProvider)

    # 注销已注册的 Provider
    result = ProviderRegistry.unregister_input("mock_input")

    assert result is True
    assert not ProviderRegistry.is_input_provider_registered("mock_input")


def test_unregister_input_provider_not_registered():
    """测试注销未注册的 InputProvider（应返回 False）"""
    ProviderRegistry.clear_all()

    result = ProviderRegistry.unregister_input("nonexistent")

    assert result is False


def test_get_registered_input_providers():
    """测试获取已注册的 InputProvider 列表"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input_1", MockInputProvider)
    ProviderRegistry.register_input("mock_input_2", MockInputProvider)

    providers = ProviderRegistry.get_registered_input_providers()

    assert isinstance(providers, list)
    assert len(providers) == 2
    assert "mock_input_1" in providers
    assert "mock_input_2" in providers


def test_is_input_provider_registered():
    """测试检查 InputProvider 是否已注册"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("mock_input", MockInputProvider)

    assert ProviderRegistry.is_input_provider_registered("mock_input") is True
    assert ProviderRegistry.is_input_provider_registered("nonexistent") is False


# =============================================================================
# OutputProvider 管理测试
# =============================================================================


def test_register_output_provider():
    """测试注册 OutputProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output", MockOutputProvider, source="test")

    assert ProviderRegistry.is_output_provider_registered("mock_output")
    assert "mock_output" in ProviderRegistry.get_registered_output_providers()


def test_register_output_provider_duplicate():
    """测试重复注册 OutputProvider（应覆盖）"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output", MockOutputProvider, source="test_source_1")

    # 重复注册（应该覆盖，不抛出异常）
    ProviderRegistry.register_output("mock_output", MockOutputProvider, source="test_source_2")

    # 验证注册成功
    assert ProviderRegistry.is_output_provider_registered("mock_output")


def test_register_output_provider_invalid_type():
    """测试注册非 OutputProvider 子类（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    class NotAProvider:
        pass

    with pytest.raises(TypeError):
        ProviderRegistry.register_output("invalid", NotAProvider)


def test_register_output_provider_not_class():
    """测试注册非类对象（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(TypeError):
        ProviderRegistry.register_output("invalid", "not_a_class")


def test_create_output_provider():
    """测试创建 OutputProvider 实例"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output", MockOutputProvider)

    config = {"test_value": "test_config"}
    provider = ProviderRegistry.create_output("mock_output", config)

    assert isinstance(provider, MockOutputProvider)
    assert provider.test_value == "test_config"
    assert provider.config == config


def test_create_output_provider_not_registered():
    """测试创建未注册的 OutputProvider（应抛出 ValueError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(ValueError, match="Unknown output provider"):
        ProviderRegistry.create_output("nonexistent", {})


def test_unregister_output_provider():
    """测试注销 OutputProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output", MockOutputProvider)

    # 注销已注册的 Provider
    result = ProviderRegistry.unregister_output("mock_output")

    assert result is True
    assert not ProviderRegistry.is_output_provider_registered("mock_output")


def test_unregister_output_provider_not_registered():
    """测试注销未注册的 OutputProvider（应返回 False）"""
    ProviderRegistry.clear_all()

    result = ProviderRegistry.unregister_output("nonexistent")

    assert result is False


def test_get_registered_output_providers():
    """测试获取已注册的 OutputProvider 列表"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output_1", MockOutputProvider)
    ProviderRegistry.register_output("mock_output_2", MockOutputProvider)

    providers = ProviderRegistry.get_registered_output_providers()

    assert isinstance(providers, list)
    assert len(providers) == 2
    assert "mock_output_1" in providers
    assert "mock_output_2" in providers


def test_is_output_provider_registered():
    """测试检查 OutputProvider 是否已注册"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_output("mock_output", MockOutputProvider)

    assert ProviderRegistry.is_output_provider_registered("mock_output") is True
    assert ProviderRegistry.is_output_provider_registered("nonexistent") is False


# =============================================================================
# DecisionProvider 管理测试
# =============================================================================


def test_register_decision_provider():
    """测试注册 DecisionProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider, source="test")

    assert ProviderRegistry.is_decision_provider_registered("mock_decision")
    assert "mock_decision" in ProviderRegistry.get_registered_decision_providers()


def test_register_decision_provider_duplicate():
    """测试重复注册 DecisionProvider（应覆盖）"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider, source="test_source_1")

    # 重复注册（应该覆盖，不抛出异常）
    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider, source="test_source_2")

    # 验证注册成功
    assert ProviderRegistry.is_decision_provider_registered("mock_decision")


def test_register_decision_provider_invalid_type():
    """测试注册非 DecisionProvider 子类（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    class NotAProvider:
        pass

    with pytest.raises(TypeError):
        ProviderRegistry.register_decision("invalid", NotAProvider)


def test_register_decision_provider_not_class():
    """测试注册非类对象（应抛出 TypeError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(TypeError):
        ProviderRegistry.register_decision("invalid", "not_a_class")


def test_create_decision_provider():
    """测试创建 DecisionProvider 实例"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider)

    config = {"test_value": "test_config"}
    provider = ProviderRegistry.create_decision("mock_decision", config)

    assert isinstance(provider, MockDecisionProvider)
    assert provider.test_value == "test_config"
    assert provider.config == config


def test_create_decision_provider_not_registered():
    """测试创建未注册的 DecisionProvider（应抛出 ValueError）"""
    ProviderRegistry.clear_all()

    with pytest.raises(ValueError, match="Unknown decision provider"):
        ProviderRegistry.create_decision("nonexistent", {})


def test_unregister_decision_provider():
    """测试注销 DecisionProvider"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider)

    # 注销已注册的 Provider
    result = ProviderRegistry.unregister_decision("mock_decision")

    assert result is True
    assert not ProviderRegistry.is_decision_provider_registered("mock_decision")


def test_unregister_decision_provider_not_registered():
    """测试注销未注册的 DecisionProvider（应返回 False）"""
    ProviderRegistry.clear_all()

    result = ProviderRegistry.unregister_decision("nonexistent")

    assert result is False


def test_get_registered_decision_providers():
    """测试获取已注册的 DecisionProvider 列表"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision_1", MockDecisionProvider)
    ProviderRegistry.register_decision("mock_decision_2", MockDecisionProvider)

    providers = ProviderRegistry.get_registered_decision_providers()

    assert isinstance(providers, list)
    assert len(providers) == 2
    assert "mock_decision_1" in providers
    assert "mock_decision_2" in providers


def test_is_decision_provider_registered():
    """测试检查 DecisionProvider 是否已注册"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider)

    assert ProviderRegistry.is_decision_provider_registered("mock_decision") is True
    assert ProviderRegistry.is_decision_provider_registered("nonexistent") is False


# =============================================================================
# 清理功能测试
# =============================================================================


def test_clear_all():
    """测试清除所有注册"""
    ProviderRegistry.clear_all()

    # 注册各种类型的 Provider
    ProviderRegistry.register_input("mock_input", MockInputProvider)
    ProviderRegistry.register_output("mock_output", MockOutputProvider)
    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider)

    # 验证已注册
    assert len(ProviderRegistry.get_registered_input_providers()) == 1
    assert len(ProviderRegistry.get_registered_output_providers()) == 1
    assert len(ProviderRegistry.get_registered_decision_providers()) == 1

    # 清除所有
    ProviderRegistry.clear_all()

    # 验证已清除
    assert len(ProviderRegistry.get_registered_input_providers()) == 0
    assert len(ProviderRegistry.get_registered_output_providers()) == 0
    assert len(ProviderRegistry.get_registered_decision_providers()) == 0


# =============================================================================
# 注册表信息测试
# =============================================================================


def test_get_registry_info():
    """测试获取注册表信息"""
    ProviderRegistry.clear_all()

    # 注册各种类型的 Provider
    ProviderRegistry.register_input("mock_input", MockInputProvider, source="test_source_input")
    ProviderRegistry.register_output("mock_output", MockOutputProvider, source="test_source_output")
    ProviderRegistry.register_decision("mock_decision", MockDecisionProvider, source="test_source_decision")

    info = ProviderRegistry.get_registry_info()

    # 验证返回结构
    assert isinstance(info, dict)
    assert "input_providers" in info
    assert "output_providers" in info
    assert "decision_providers" in info

    # 验证 InputProvider 信息
    assert "mock_input" in info["input_providers"]
    assert info["input_providers"]["mock_input"]["class"] == "MockInputProvider"
    assert info["input_providers"]["mock_input"]["source"] == "test_source_input"

    # 验证 OutputProvider 信息
    assert "mock_output" in info["output_providers"]
    assert info["output_providers"]["mock_output"]["class"] == "MockOutputProvider"
    assert info["output_providers"]["mock_output"]["source"] == "test_source_output"

    # 验证 DecisionProvider 信息
    assert "mock_decision" in info["decision_providers"]
    assert info["decision_providers"]["mock_decision"]["class"] == "MockDecisionProvider"
    assert info["decision_providers"]["mock_decision"]["source"] == "test_source_decision"


def test_get_registry_info_empty():
    """测试获取空注册表信息"""
    ProviderRegistry.clear_all()

    info = ProviderRegistry.get_registry_info()

    assert info == {"input_providers": {}, "output_providers": {}, "decision_providers": {}}


# =============================================================================
# 错误处理测试
# =============================================================================


def test_create_provider_with_init_error():
    """测试创建 Provider 时初始化失败（异常应传播）"""
    ProviderRegistry.clear_all()

    ProviderRegistry.register_input("failing_provider", FailingMockProvider)

    # 创建时应该抛出 ValueError
    with pytest.raises(ValueError, match="Initialization failed"):
        ProviderRegistry.create_input("failing_provider", {})


# =============================================================================
# 真实 Provider 自动注册测试
# =============================================================================


def test_builtin_providers_auto_registration():
    """测试内置 Provider 自动注册"""
    ProviderRegistry.clear_all()

    # 导入所有 Provider 模块以触发自动注册

    # 验证至少有一些内置 Provider 被注册
    input_providers = ProviderRegistry.get_registered_input_providers()
    output_providers = ProviderRegistry.get_registered_output_providers()
    decision_providers = ProviderRegistry.get_registered_decision_providers()

    # 应该有内置 Provider 注册
    assert len(input_providers) > 0, "应该有内置 InputProvider 被注册"
    assert len(output_providers) > 0, "应该有内置 OutputProvider 被注册"
    assert len(decision_providers) > 0, "应该有内置 DecisionProvider 被注册"

    # 检查一些已知的 Provider
    assert "console_input" in input_providers
    assert "maicore" in decision_providers
    assert "subtitle" in output_providers


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
