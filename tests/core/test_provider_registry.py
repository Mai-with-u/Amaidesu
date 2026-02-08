"""
ProviderRegistry 测试

测试 Provider 注册表的以下功能：
1. Provider 注册（Input/Decision/Output）
2. Provider 创建
3. Provider 查询
4. 错误处理
"""

import pytest
from src.core.provider_registry import ProviderRegistry
from src.core.base.input_provider import InputProvider
from src.core.base.decision_provider import DecisionProvider
from src.core.base.output_provider import OutputProvider
from src.core.base.raw_data import RawData


class MockInputProvider(InputProvider):
    """模拟 InputProvider 用于测试"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.provider_type = "mock_input"

    async def _collect_data(self) -> RawData:
        """收集数据（必须实现的抽象方法）"""
        return RawData(content="test", data_type="text", metadata={})


class MockDecisionProvider(DecisionProvider):
    """模拟 DecisionProvider 用于测试"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.provider_type = "mock_decision"

    async def decide(self, message):
        return None

    async def setup(self, event_bus, config=None, dependencies=None):
        pass

    async def cleanup(self):
        pass


class MockOutputProvider(OutputProvider):
    """模拟 OutputProvider 用于测试"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.provider_type = "mock_output"

    async def _render_internal(self, parameters):
        pass


@pytest.fixture(autouse=True)
def clear_registry():
    """每个测试前清空注册表

    注意：此清空会影响其他测试文件，因为它不会自动恢复内置 Provider。
    如果测试失败，可能是因为注册表被清空导致其他测试无法找到 Provider。
    """
    ProviderRegistry.clear_all()
    yield
    # 不在测试后清空，避免影响其他测试文件


class TestInputProviderRegistry:
    """测试 InputProvider 注册"""

    def test_register_input_provider(self):
        """测试注册 InputProvider"""
        ProviderRegistry.register_input("test_input", MockInputProvider, source="test")
        assert ProviderRegistry.is_input_provider_registered("test_input")

    def test_register_input_provider_overwrites(self):
        """测试覆盖已注册的 Provider"""
        from src.core.base.input_provider import InputProvider
        from src.core.base.raw_data import RawData

        class AnotherInputProvider(InputProvider):
            def __init__(self, config: dict):
                super().__init__(config)

            async def _collect_data(self) -> RawData:
                return RawData(content="test", data_type="text", metadata={})

        ProviderRegistry.register_input("test_input", MockInputProvider, source="test1")
        ProviderRegistry.register_input("test_input", AnotherInputProvider, source="test2")
        # 应该覆盖成功，并发出警告
        assert ProviderRegistry.is_input_provider_registered("test_input")

    def test_register_input_provider_invalid_type(self):
        """测试注册无效类型（非 InputProvider 子类）"""
        with pytest.raises(TypeError):
            ProviderRegistry.register_input("invalid", str)  # str 不是 InputProvider

    def test_get_registered_input_providers(self):
        """测试获取已注册的 InputProvider 列表"""
        ProviderRegistry.register_input("test1", MockInputProvider)
        ProviderRegistry.register_input("test2", MockInputProvider)
        providers = ProviderRegistry.get_registered_input_providers()
        assert "test1" in providers
        assert "test2" in providers

    def test_create_input_provider(self):
        """测试创建 InputProvider 实例"""
        ProviderRegistry.register_input("test_input", MockInputProvider)
        provider = ProviderRegistry.create_input("test_input", {"key": "value"})
        assert isinstance(provider, MockInputProvider)

    def test_create_input_provider_unknown(self):
        """测试创建不存在的 Provider"""
        with pytest.raises(ValueError, match="Unknown input provider"):
            ProviderRegistry.create_input("unknown", {})

    def test_unregister_input_provider(self):
        """测试注销 InputProvider"""
        ProviderRegistry.register_input("test_input", MockInputProvider)
        assert ProviderRegistry.is_input_provider_registered("test_input")
        result = ProviderRegistry.unregister_input("test_input")
        assert result is True
        assert not ProviderRegistry.is_input_provider_registered("test_input")

    def test_unregister_input_provider_not_exists(self):
        """测试注销不存在的 Provider"""
        result = ProviderRegistry.unregister_input("non_existent")
        assert result is False


class TestDecisionProviderRegistry:
    """测试 DecisionProvider 注册"""

    def test_register_decision_provider(self):
        """测试注册 DecisionProvider"""
        ProviderRegistry.register_decision("test_decision", MockDecisionProvider)
        assert ProviderRegistry.is_decision_provider_registered("test_decision")

    def test_create_decision_provider(self):
        """测试创建 DecisionProvider 实例"""
        ProviderRegistry.register_decision("test_decision", MockDecisionProvider)
        provider = ProviderRegistry.create_decision("test_decision", {"key": "value"})
        assert isinstance(provider, MockDecisionProvider)

    def test_create_decision_provider_unknown(self):
        """测试创建不存在的 Provider"""
        with pytest.raises(ValueError, match="Unknown decision provider"):
            ProviderRegistry.create_decision("unknown", {})

    def test_get_registered_decision_providers(self):
        """测试获取已注册的 DecisionProvider 列表"""
        ProviderRegistry.register_decision("test1", MockDecisionProvider)
        ProviderRegistry.register_decision("test2", MockDecisionProvider)
        providers = ProviderRegistry.get_registered_decision_providers()
        assert "test1" in providers
        assert "test2" in providers


class TestOutputProviderRegistry:
    """测试 OutputProvider 注册"""

    def test_register_output_provider(self):
        """测试注册 OutputProvider"""
        ProviderRegistry.register_output("test_output", MockOutputProvider)
        assert ProviderRegistry.is_output_provider_registered("test_output")

    def test_create_output_provider(self):
        """测试创建 OutputProvider 实例"""
        ProviderRegistry.register_output("test_output", MockOutputProvider)
        provider = ProviderRegistry.create_output("test_output", {"key": "value"})
        assert isinstance(provider, MockOutputProvider)

    def test_create_output_provider_unknown(self):
        """测试创建不存在的 Provider"""
        with pytest.raises(ValueError, match="Unknown output provider"):
            ProviderRegistry.create_output("unknown", {})

    def test_get_registered_output_providers(self):
        """测试获取已注册的 OutputProvider 列表"""
        ProviderRegistry.register_output("test1", MockOutputProvider)
        ProviderRegistry.register_output("test2", MockOutputProvider)
        providers = ProviderRegistry.get_registered_output_providers()
        assert "test1" in providers
        assert "test2" in providers


class TestRegistryInfo:
    """测试注册表信息"""

    def test_get_registry_info(self):
        """测试获取注册表信息"""
        ProviderRegistry.register_input("test_input", MockInputProvider, source="test_source")
        ProviderRegistry.register_decision("test_decision", MockDecisionProvider)
        ProviderRegistry.register_output("test_output", MockOutputProvider)

        info = ProviderRegistry.get_registry_info()

        assert "input_providers" in info
        assert "decision_providers" in info
        assert "output_providers" in info

        assert "test_input" in info["input_providers"]
        assert "test_decision" in info["decision_providers"]
        assert "test_output" in info["output_providers"]

        # 检查源信息
        assert info["input_providers"]["test_input"]["source"] == "test_source"

    def test_get_all_providers(self):
        """测试获取所有 Provider"""
        ProviderRegistry.register_input("test_input", MockInputProvider)
        ProviderRegistry.register_decision("test_decision", MockDecisionProvider)
        ProviderRegistry.register_output("test_output", MockOutputProvider)

        all_providers = ProviderRegistry.get_all_providers()

        assert "test_input" in all_providers["input"]
        assert "test_decision" in all_providers["decision"]
        assert "test_output" in all_providers["output"]


class TestClearAll:
    """测试清空注册表"""

    def test_clear_all(self):
        """测试清空所有 Provider"""
        ProviderRegistry.register_input("test_input", MockInputProvider)
        ProviderRegistry.register_decision("test_decision", MockDecisionProvider)
        ProviderRegistry.register_output("test_output", MockOutputProvider)

        ProviderRegistry.clear_all()

        assert len(ProviderRegistry.get_registered_input_providers()) == 0
        assert len(ProviderRegistry.get_registered_decision_providers()) == 0
        assert len(ProviderRegistry.get_registered_output_providers()) == 0
