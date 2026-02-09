"""
AdapterFactory 测试
"""

import pytest
from src.domains.output.adapter_factory import AdapterFactory
from src.domains.output.adapters.base import PlatformAdapter


class MockAdapter(PlatformAdapter):
    """Mock 适配器用于测试"""

    def __init__(self, config):
        super().__init__("mock", config)

    async def connect(self) -> bool:
        self._is_connected = True
        return True

    async def disconnect(self) -> bool:
        self._is_connected = False
        return True

    async def set_parameters(self, params: dict) -> bool:
        return True

    async def trigger_hotkey(self, hotkey_name: str) -> bool:
        return True


class InvalidAdapter:
    """不是 PlatformAdapter 的子类"""

    def __init__(self, config):
        pass


class TestAdapterFactory:
    """测试 AdapterFactory"""

    def test_list_available_adapters(self):
        """测试列出可用的适配器"""
        adapters = AdapterFactory.list_available_adapters()

        assert "vts" in adapters
        assert "vrchat" in adapters
        assert "vrc" in adapters

    def test_register_adapter_valid(self):
        """测试注册有效的适配器"""
        AdapterFactory.register_adapter("mock", MockAdapter)

        adapters = AdapterFactory.list_available_adapters()
        assert "mock" in adapters

    def test_register_adapter_invalid(self):
        """测试注册无效的适配器（不是 PlatformAdapter 子类）"""
        with pytest.raises(TypeError):
            AdapterFactory.register_adapter("invalid", InvalidAdapter)

    def test_create_vts_adapter(self):
        """测试创建 VTS 适配器"""
        config = {
            "vts_host": "127.0.0.1",
            "vts_port": 8001,
        }

        adapter = AdapterFactory.create("vts", config)

        assert adapter is not None
        assert adapter.adapter_name == "vts"

    def test_create_vrchat_adapter(self):
        """测试创建 VRChat 适配器"""
        config = {
            "vrc_host": "127.0.0.1",
            "vrc_in_port": 9001,
            "vrc_out_port": 9000,
        }

        adapter = AdapterFactory.create("vrchat", config)

        assert adapter is not None
        assert adapter.adapter_name == "vrchat"

    def test_create_vrc_adapter_alias(self):
        """测试创建 VRC 适配器（别名）"""
        config = {
            "vrc_host": "127.0.0.1",
            "vrc_in_port": 9001,
            "vrc_out_port": 9000,
        }

        adapter = AdapterFactory.create("vrc", config)

        assert adapter is not None
        assert adapter.adapter_name == "vrchat"

    def test_create_unknown_adapter(self):
        """测试创建未知的适配器"""
        adapter = AdapterFactory.create("unknown", {})

        assert adapter is None

    def test_create_with_registered_adapter(self):
        """测试使用已注册的适配器"""
        AdapterFactory.register_adapter("mock_test", MockAdapter)

        config = {"test": "config"}
        adapter = AdapterFactory.create("mock_test", config)

        assert adapter is not None
        assert isinstance(adapter, MockAdapter)

    def test_create_with_invalid_config(self):
        """测试使用无效配置创建适配器"""
        # VTS 适配器不会在 __init__ 时验证配置
        # 所以会成功创建，但后续连接可能会失败
        adapter = AdapterFactory.create("vts", {})

        # VTSAdapter 会成功创建（配置验证在连接时）
        assert adapter is not None
        assert adapter.adapter_name == "vts"

    def test_list_adapters_includes_registered(self):
        """测试列出适配器包含已注册的适配器"""
        # 首先清理可能存在的 mock_test
        if "mock_test" in AdapterFactory._adapters:
            del AdapterFactory._adapters["mock_test"]

        initial_count = len(AdapterFactory.list_available_adapters())

        AdapterFactory.register_adapter("mock_test", MockAdapter)

        new_count = len(AdapterFactory.list_available_adapters())

        assert new_count == initial_count + 1

    def test_create_adapter_error_handling(self):
        """测试创建适配器时的错误处理"""

        # 创建一个会抛出异常的适配器类
        class FailingAdapter(PlatformAdapter):
            def __init__(self, config):
                raise ValueError("Test error")

        AdapterFactory.register_adapter("failing", FailingAdapter)

        adapter = AdapterFactory.create("failing", {})

        # 应该返回 None 而不是抛出异常
        assert adapter is None
