"""
测试 PlatformAdapter 基类

运行: uv run pytest tests/domains/output/adapters/test_base_adapter.py -v
"""

import pytest
from abc import ABC
from src.domains.output.adapters.base import PlatformAdapter


# =============================================================================
# 抽象类测试
# =============================================================================


def test_platform_adapter_is_abstract():
    """测试 PlatformAdapter 是抽象类"""
    assert issubclass(PlatformAdapter, ABC)


def test_platform_adapter_cannot_instantiate():
    """测试不能直接实例化 PlatformAdapter"""
    with pytest.raises(TypeError):
        PlatformAdapter("test", {})


# =============================================================================
# 测试用子类
# =============================================================================


class MockPlatformAdapter(PlatformAdapter):
    """用于测试的 Mock Adapter"""

    def __init__(self, adapter_name: str, config: dict):
        super().__init__(adapter_name, config)
        self.connect_called = False
        self.disconnect_called = False
        self.set_params_called = False

    async def connect(self) -> bool:
        self.connect_called = True
        self._is_connected = True
        return True

    async def disconnect(self) -> bool:
        self.disconnect_called = True
        self._is_connected = False
        return True

    async def set_parameters(self, params: dict) -> bool:
        self.set_params_called = True
        return True


# =============================================================================
# 初始化测试
# =============================================================================


def test_adapter_initialization():
    """测试 Adapter 初始化"""
    adapter = MockPlatformAdapter("test_adapter", {"key": "value"})

    assert adapter.adapter_name == "test_adapter"
    assert adapter.config == {"key": "value"}
    assert adapter.is_connected is False


def test_adapter_default_config():
    """测试默认配置"""
    adapter = MockPlatformAdapter("test", {})

    assert adapter.adapter_name == "test"
    assert adapter.config == {}


# =============================================================================
# is_connected 属性测试
# =============================================================================


def test_is_connected_property():
    """测试 is_connected 属性"""
    adapter = MockPlatformAdapter("test", {})

    # 初始状态未连接
    assert adapter.is_connected is False

    # 模拟连接
    adapter._is_connected = True
    assert adapter.is_connected is True

    # 模拟断开
    adapter._is_connected = False
    assert adapter.is_connected is False


# =============================================================================
# translate_params() 测试
# =============================================================================


def test_translate_params_default():
    """测试默认参数翻译（直接返回）"""
    adapter = MockPlatformAdapter("test", {})

    params = {"smile": 0.8, "eye_open": 0.9}
    translated = adapter.translate_params(params)

    # 默认实现直接返回副本
    assert translated == params
    assert translated is not params  # 应该是副本


def test_translate_params_empty():
    """测试空参数翻译"""
    adapter = MockPlatformAdapter("test", {})

    params = {}
    translated = adapter.translate_params(params)

    assert translated == {}


def test_translate_params_modification():
    """测试翻译后修改不影响原参数"""
    adapter = MockPlatformAdapter("test", {})

    params = {"smile": 0.8}
    translated = adapter.translate_params(params)

    # 修改翻译后的参数
    translated["smile"] = 0.5

    # 原参数不应改变
    assert params["smile"] == 0.8


# =============================================================================
# _set_parameter_safe() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_set_parameter_safe_default():
    """测试默认的 _set_parameter_safe 实现"""
    adapter = MockPlatformAdapter("test", {})

    # 默认实现总是返回 True
    result = await adapter._set_parameter_safe("test_param", 0.5)
    assert result is True


# =============================================================================
# 子类功能测试
# =============================================================================


@pytest.mark.asyncio
async def test_mock_adapter_connect():
    """测试 Mock Adapter 连接"""
    adapter = MockPlatformAdapter("test", {})

    assert adapter.is_connected is False

    result = await adapter.connect()

    assert result is True
    assert adapter.is_connected is True
    assert adapter.connect_called is True


@pytest.mark.asyncio
async def test_mock_adapter_disconnect():
    """测试 Mock Adapter 断开"""
    adapter = MockPlatformAdapter("test", {})

    # 先连接
    await adapter.connect()
    assert adapter.is_connected is True

    # 再断开
    result = await adapter.disconnect()

    assert result is True
    assert adapter.is_connected is False
    assert adapter.disconnect_called is True


@pytest.mark.asyncio
async def test_mock_adapter_set_parameters():
    """测试 Mock Adapter 设置参数"""
    adapter = MockPlatformAdapter("test", {})

    params = {"smile": 0.8, "eye_open": 0.9}
    result = await adapter.set_parameters(params)

    assert result is True
    assert adapter.set_params_called is True


# =============================================================================
# 自定义翻译测试
# =============================================================================


class CustomTranslateAdapter(MockPlatformAdapter):
    """自定义参数翻译的 Adapter"""

    def translate_params(self, abstract_params: dict) -> dict:
        """自定义翻译逻辑"""
        # 将抽象参数名映射到平台特定参数名
        mapping = {
            "smile": "platform_smile",
            "eye_open": "platform_eye_open",
        }

        translated = {}
        for key, value in abstract_params.items():
            platform_key = mapping.get(key, key)
            translated[platform_key] = value

        return translated


def test_custom_translate_params():
    """测试自定义参数翻译"""
    adapter = CustomTranslateAdapter("test", {})

    abstract_params = {"smile": 0.8, "eye_open": 0.9, "custom": 0.5}
    translated = adapter.translate_params(abstract_params)

    assert translated == {
        "platform_smile": 0.8,
        "platform_eye_open": 0.9,
        "custom": 0.5,  # 没有映射，保持原样
    }


# =============================================================================
# 边界条件测试
# =============================================================================


@pytest.mark.asyncio
async def test_connect_failure():
    """测试连接失败处理"""
    class FailingAdapter(MockPlatformAdapter):
        async def connect(self) -> bool:
            self._is_connected = False  # 连接失败
            return False

    adapter = FailingAdapter("test", {})
    result = await adapter.connect()

    assert result is False
    assert adapter.is_connected is False


@pytest.mark.asyncio
async def test_disconnect_failure():
    """测试断开失败处理"""
    class FailingDisconnectAdapter(MockPlatformAdapter):
        async def disconnect(self) -> bool:
            # 断开失败但状态未改变
            return False

    adapter = FailingDisconnectAdapter("test", {})
    await adapter.connect()
    adapter._is_connected = True  # 模拟已连接

    result = await adapter.disconnect()

    assert result is False
    # 失败时连接状态可能未改变（取决于实现）


@pytest.mark.asyncio
async def test_set_parameters_not_connected():
    """测试未连接时设置参数"""
    adapter = MockPlatformAdapter("test", {})

    # 未连接状态
    assert adapter.is_connected is False

    params = {"smile": 0.8}
    # 子类可以检查 is_connected 并拒绝操作
    result = await adapter.set_parameters(params)

    # Mock adapter 总是返回 True，真实实现可能返回 False
    assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
