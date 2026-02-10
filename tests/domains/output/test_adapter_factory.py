"""
测试 AdapterFactory (适配器工厂)

运行: uv run pytest tests/domains/output/test_adapter_factory.py -v
"""

import pytest
from unittest.mock import Mock, patch

from src.domains.output.adapter_factory import AdapterFactory
from src.domains.output.adapters.base import PlatformAdapter


# =============================================================================
# 测试用 Adapter
# =============================================================================


class MockAdapter(PlatformAdapter):
    """用于测试的 Mock Adapter"""

    def __init__(self, config: dict):
        super().__init__("mock", config)

    async def connect(self) -> bool:
        self._is_connected = True
        return True

    async def disconnect(self) -> bool:
        self._is_connected = False
        return True

    async def set_parameters(self, params: dict) -> bool:
        return True


class FailingMockAdapter(PlatformAdapter):
    """初始化会失败的 Mock Adapter"""

    def __init__(self, config: dict):
        super().__init__("failing", config)
        raise Exception("初始化失败")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> bool:
        return True

    async def set_parameters(self, params: dict) -> bool:
        return True


# =============================================================================
# 内置适配器测试
# =============================================================================


def test_builtin_adapters():
    """测试内置适配器列表"""
    adapters = AdapterFactory.list_available_adapters()

    assert "vts" in adapters
    assert "vrchat" in adapters
    assert "vrc" in adapters  # 别名


def test_vts_adapter_exists():
    """测试 VTS 适配器存在"""
    assert "vts" in AdapterFactory._adapters

    # 尝试获取类
    adapter_class = AdapterFactory._adapters["vts"]
    assert adapter_class is not None


def test_vrchat_adapter_exists():
    """测试 VRChat 适配器存在"""
    assert "vrchat" in AdapterFactory._adapters

    adapter_class = AdapterFactory._adapters["vrchat"]
    assert adapter_class is not None


# =============================================================================
# create() 测试
# =============================================================================


@patch("src.domains.output.adapter_factory.pyvts", None)
def test_create_vts_adapter_missing_pyvts():
    """测试创建 VTS 适配器（缺少 pyvts）"""
    config = {
        "plugin_name": "Test",
        "developer": "Test",
        "vts_host": "localhost",
        "vts_port": 8001,
    }

    # 由于缺少 pyvts，应该抛出 ImportError
    with pytest.raises(ImportError):
        AdapterFactory.create("vts", config)


def test_create_unknown_adapter():
    """测试创建未知适配器"""
    result = AdapterFactory.create("unknown_adapter", {})

    # 未知适配器应该返回 None
    assert result is None


# =============================================================================
# register_adapter() 测试
# =============================================================================


def test_register_adapter():
    """测试注册新适配器"""
    # 注册测试适配器
    AdapterFactory.register_adapter("mock", MockAdapter)

    # 验证已注册
    assert "mock" in AdapterFactory.list_available_adapters()

    # 创建实例
    adapter = AdapterFactory.create("mock", {})
    assert adapter is not None
    assert isinstance(adapter, MockAdapter)


def test_register_adapter_not_subclass():
    """测试注册非 PlatformAdapter 子类"""

    class NotAnAdapter:
        pass

    with pytest.raises(TypeError, match="必须是 PlatformAdapter 的子类"):
        AdapterFactory.register_adapter("invalid", NotAnAdapter)


def test_register_adapter_override():
    """测试覆盖已存在的适配器"""
    # 注册新适配器覆盖内置适配器
    AdapterFactory.register_adapter("mock", MockAdapter)

    # 再次注册
    AdapterFactory.register_adapter("mock", FailingMockAdapter)

    # 应该被覆盖
    assert AdapterFactory._adapters["mock"] == FailingMockAdapter


# =============================================================================
# list_available_adapters() 测试
# =============================================================================


def test_list_available_adapters_returns_list():
    """测试返回列表类型"""
    adapters = AdapterFactory.list_available_adapters()

    assert isinstance(adapters, list)
    assert len(adapters) > 0


def test_list_available_adapters_includes_aliases():
    """测试列表包含别名"""
    adapters = AdapterFactory.list_available_adapters()

    # vrc 是 vrchat 的别名
    assert "vrchat" in adapters
    assert "vrc" in adapters


# =============================================================================
# 创建失败测试
# =============================================================================


def test_create_adapter_init_failure():
    """测试适配器初始化失败"""
    # 注册会失败的适配器
    AdapterFactory.register_adapter("failing", FailingMockAdapter)

    # 创建应该捕获异常并返回 None
    result = AdapterFactory.create("failing", {})

    assert result is None


# =============================================================================
# 边界条件测试
# =============================================================================


def test_create_empty_config():
    """测试空配置创建"""
    # 使用 Mock 适配器（测试前先注册）
    AdapterFactory.register_adapter("mock", MockAdapter)

    adapter = AdapterFactory.create("mock", {})

    assert adapter is not None
    assert adapter.config == {}


def test_create_complex_config():
    """测试复杂配置"""
    AdapterFactory.register_adapter("mock", MockAdapter)

    config = {
        "str_key": "value",
        "int_key": 123,
        "float_key": 45.6,
        "bool_key": True,
        "list_key": [1, 2, 3],
        "dict_key": {"nested": "value"},
    }

    adapter = AdapterFactory.create("mock", config)

    assert adapter is not None
    assert adapter.config == config


# =============================================================================
# 类方法测试
# =============================================================================


def test_adapter_factory_class_methods():
    """测试 AdapterFactory 类方法"""
    # 验证所有预期的方法存在
    assert hasattr(AdapterFactory, "register_adapter")
    assert callable(AdapterFactory.register_adapter)

    assert hasattr(AdapterFactory, "create")
    assert callable(AdapterFactory.create)

    assert hasattr(AdapterFactory, "list_available_adapters")
    assert callable(AdapterFactory.list_available_adapters)


# =============================================================================
# 集成测试
# =============================================================================


def test_full_adapter_lifecycle():
    """测试完整的适配器生命周期"""
    # 1. 注册适配器
    AdapterFactory.register_adapter("mock", MockAdapter)

    # 2. 验证已注册
    assert "mock" in AdapterFactory.list_available_adapters()

    # 3. 创建实例
    adapter = AdapterFactory.create("mock", {"test": "config"})
    assert adapter is not None
    assert adapter.adapter_name == "mock"

    # 4. 验证是正确的类型
    assert isinstance(adapter, PlatformAdapter)
    assert isinstance(adapter, MockAdapter)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
