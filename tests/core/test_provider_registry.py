"""
ProviderRegistry 测试

测试 Provider 注册表的以下功能：
1. Provider 注册（Input/Decision/Output）
2. Provider 创建
3. Provider 查询
4. 错误处理
5. 显式注册模式（新增）
"""

import pytest

from src.modules.registry import ProviderRegistry
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.output_provider import OutputProvider
from src.modules.types.base.raw_data import RawData


class MockInputProvider(InputProvider):
    """模拟 InputProvider 用于测试"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.provider_type = "mock_input"

    async def _collect_data(self) -> RawData:
        """收集数据（必须实现的抽象方法）"""
        return RawData(content="test", data_type="text", metadata={})

    @classmethod
    def get_registration_info(cls):
        """获取注册信息"""
        return {"layer": "input", "name": "mock_input_explicit", "class": cls, "source": "test:mock_input"}


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

    @classmethod
    def get_registration_info(cls):
        """获取注册信息"""
        return {"layer": "decision", "name": "mock_decision_explicit", "class": cls, "source": "test:mock_decision"}


class MockOutputProvider(OutputProvider):
    """模拟 OutputProvider 用于测试"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.provider_type = "mock_output"

    async def _render_internal(self, parameters):
        pass

    @classmethod
    def get_registration_info(cls):
        """获取注册信息"""
        return {"layer": "output", "name": "mock_output_explicit", "class": cls, "source": "test:mock_output"}


@pytest.fixture(autouse=True)
def clear_registry():
    """每个测试前清空注册表并在测试后恢复

    注意：此清空会影响其他测试文件，因此我们在测试后恢复内置 Provider。
    """
    # 保存当前的注册状态
    saved_input = dict(ProviderRegistry._input_providers)
    saved_decision = dict(ProviderRegistry._decision_providers)
    saved_output = dict(ProviderRegistry._output_providers)
    saved_schemas = dict(ProviderRegistry._config_schemas)

    # 清空注册表
    ProviderRegistry.clear_all()
    yield

    # 恢复注册状态
    ProviderRegistry._input_providers.clear()
    ProviderRegistry._decision_providers.clear()
    ProviderRegistry._output_providers.clear()
    ProviderRegistry._config_schemas.clear()

    ProviderRegistry._input_providers.update(saved_input)
    ProviderRegistry._decision_providers.update(saved_decision)
    ProviderRegistry._output_providers.update(saved_output)
    ProviderRegistry._config_schemas.update(saved_schemas)


class TestInputProviderRegistry:
    """测试 InputProvider 注册"""

    def test_register_input_provider(self):
        """测试注册 InputProvider"""
        ProviderRegistry.register_input("test_input", MockInputProvider, source="test")
        assert ProviderRegistry.is_input_provider_registered("test_input")

    def test_register_input_provider_overwrites(self):
        """测试覆盖已注册的 Provider"""
        from src.modules.types.base.input_provider import InputProvider
        from src.modules.types.base.raw_data import RawData

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


class TestExplicitRegistration:
    """测试显式注册模式（新增功能）"""

    def test_register_from_info_input(self):
        """测试从注册信息字典注册 InputProvider"""
        info = MockInputProvider.get_registration_info()
        ProviderRegistry.register_from_info(info)

        assert ProviderRegistry.is_input_provider_registered("mock_input_explicit")

        # 验证可以创建实例
        provider = ProviderRegistry.create_input("mock_input_explicit", {})
        assert isinstance(provider, MockInputProvider)

    def test_register_from_info_decision(self):
        """测试从注册信息字典注册 DecisionProvider"""
        info = MockDecisionProvider.get_registration_info()
        ProviderRegistry.register_from_info(info)

        assert ProviderRegistry.is_decision_provider_registered("mock_decision_explicit")

        # 验证可以创建实例
        provider = ProviderRegistry.create_decision("mock_decision_explicit", {})
        assert isinstance(provider, MockDecisionProvider)

    def test_register_from_info_output(self):
        """测试从注册信息字典注册 OutputProvider"""
        info = MockOutputProvider.get_registration_info()
        ProviderRegistry.register_from_info(info)

        assert ProviderRegistry.is_output_provider_registered("mock_output_explicit")

        # 验证可以创建实例
        provider = ProviderRegistry.create_output("mock_output_explicit", {})
        assert isinstance(provider, MockOutputProvider)

    def test_register_from_info_missing_field(self):
        """测试注册信息缺少必需字段"""
        incomplete_info = {
            "layer": "input",
            "name": "test",
            # 缺少 "class" 字段
        }

        with pytest.raises(ValueError, match="注册信息缺少必需字段"):
            ProviderRegistry.register_from_info(incomplete_info)

    def test_register_from_info_invalid_layer(self):
        """测试无效的 Provider 域"""
        invalid_info = {"layer": "invalid_layer", "name": "test", "class": MockInputProvider}

        with pytest.raises(ValueError, match="无效的 Provider 域"):
            ProviderRegistry.register_from_info(invalid_info)

    def test_register_provider_class(self):
        """测试直接从 Provider 类注册（便捷方法）"""
        ProviderRegistry.register_provider_class(MockInputProvider)

        assert ProviderRegistry.is_input_provider_registered("mock_input_explicit")

        # 验证可以创建实例
        provider = ProviderRegistry.create_input("mock_input_explicit", {})
        assert isinstance(provider, MockInputProvider)

    def test_register_provider_class_no_method(self):
        """测试 Provider 类未实现 get_registration_info()"""

        class IncompleteProvider(InputProvider):
            def __init__(self, config: dict):
                super().__init__(config)

            async def _collect_data(self):
                return RawData(content="test", data_type="text", metadata={})

        with pytest.raises(NotImplementedError, match="get_registration_info"):
            ProviderRegistry.register_provider_class(IncompleteProvider)

    def test_explicit_registration_isolation(self):
        """测试显式注册支持隔离（多次调用不会冲突）"""
        # 第一次注册
        info1 = MockInputProvider.get_registration_info()
        ProviderRegistry.register_from_info(info1)
        assert ProviderRegistry.is_input_provider_registered("mock_input_explicit")

        # 清空
        ProviderRegistry.clear_all()
        assert not ProviderRegistry.is_input_provider_registered("mock_input_explicit")

        # 第二次注册（应该成功）
        info2 = MockInputProvider.get_registration_info()
        ProviderRegistry.register_from_info(info2)
        assert ProviderRegistry.is_input_provider_registered("mock_input_explicit")
