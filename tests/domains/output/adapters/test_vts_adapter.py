"""
测试 VTSAdapter (VTube Studio 适配器)

运行: uv run pytest tests/domains/output/adapters/test_vts_adapter.py -v

注意：此测试使用 Mock 避免真实的 VTS 连接
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock


# =============================================================================
# 配置 Fixtures
# =============================================================================


@pytest.fixture
def vts_config():
    """测试用 VTS 配置"""
    return {
        "plugin_name": "TestPlugin",
        "developer": "TestDeveloper",
        "authentication_token_path": "./test_token.txt",
        "vts_host": "localhost",
        "vts_port": 8001,
        "vts_api_name": "VTubeStudioPublicAPI",
        "vts_api_version": "1.0",
    }


@pytest.fixture
def mock_pyvts():
    """Mock pyvts 模块"""
    with patch("src.domains.output.adapters.vts.vts_adapter.pyvts") as mock:
        yield mock


# =============================================================================
# 创建和初始化测试
# =============================================================================


def test_vts_adapter_creation(vts_config, mock_pyvts):
    """测试 VTSAdapter 创建"""
    # Mock pyvts 存在
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    assert adapter.adapter_name == "vts"
    assert adapter.config == vts_config
    assert adapter.is_connected is False


def test_vts_adapter_missing_pyvts(vts_config):
    """测试缺少 pyvts 时抛出错误"""
    # Mock pyvts 为 None
    with patch("src.domains.output.adapters.vts.vts_adapter.pyvts", None):
        from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

        with pytest.raises(ImportError, match="pyvts is required"):
            VTSAdapter(vts_config)


# =============================================================================
# translate_params() 测试
# =============================================================================


def test_translate_params_smile(vts_config, mock_pyvts):
    """测试翻译微笑参数"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {"smile": 0.8}
    translated = adapter.translate_params(abstract_params)

    assert translated == {"MouthSmile": 0.8}


def test_translate_params_eye_open(vts_config, mock_pyvts):
    """测试翻译眼睛开合参数（同时设置左右眼）"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {"eye_open": 0.9}
    translated = adapter.translate_params(abstract_params)

    assert translated == {
        "EyeOpenLeft": 0.9,
        "EyeOpenRight": 0.9,
    }


def test_translate_params_mouth_open(vts_config, mock_pyvts):
    """测试翻译嘴巴张开参数"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {"mouth_open": 0.5}
    translated = adapter.translate_params(abstract_params)

    assert translated == {"MouthOpen": 0.5}


def test_translate_params_brow_down(vts_config, mock_pyvts):
    """测试翻译眉毛参数（映射为 None，不传递）"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {"brow_down": 0.3}
    translated = adapter.translate_params(abstract_params)

    # brow_down 映射为 None，不应该出现在结果中
    assert translated == {}


def test_translate_params_multiple(vts_config, mock_pyvts):
    """测试翻译多个参数"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {
        "smile": 0.8,
        "eye_open": 0.9,
        "mouth_open": 0.5,
    }
    translated = adapter.translate_params(abstract_params)

    expected = {
        "MouthSmile": 0.8,
        "EyeOpenLeft": 0.9,
        "EyeOpenRight": 0.9,
        "MouthOpen": 0.5,
    }
    assert translated == expected


def test_translate_params_unknown(vts_config, mock_pyvts):
    """测试未知参数（不在映射表中）"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    abstract_params = {"unknown_param": 0.5}
    translated = adapter.translate_params(abstract_params)

    # 未知参数不在映射中，不传递
    assert translated == {}


# =============================================================================
# connect() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_connect_success(vts_config, mock_pyvts):
    """测试成功连接到 VTS"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.connect = AsyncMock()
    mock_vts_instance.request_authenticate_token = AsyncMock()
    mock_vts_instance.request_authenticate = AsyncMock(return_value=True)
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    result = await adapter.connect()

    assert result is True
    assert adapter.is_connected is True
    assert adapter.vts == mock_vts_instance

    # 验证调用顺序
    mock_vts_instance.connect.assert_called_once()
    mock_vts_instance.request_authenticate_token.assert_called_once()
    mock_vts_instance.request_authenticate.assert_called_once()


@pytest.mark.asyncio
async def test_connect_auth_failure(vts_config, mock_pyvts):
    """测试认证失败"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.connect = AsyncMock()
    mock_vts_instance.request_authenticate_token = AsyncMock()
    mock_vts_instance.request_authenticate = AsyncMock(return_value=False)
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    result = await adapter.connect()

    assert result is False
    assert adapter.is_connected is False


@pytest.mark.asyncio
async def test_connect_exception(vts_config, mock_pyvts):
    """测试连接时抛出异常"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.connect = AsyncMock(side_effect=Exception("连接失败"))
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    result = await adapter.connect()

    assert result is False
    assert adapter.is_connected is False


# =============================================================================
# disconnect() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_disconnect_success(vts_config, mock_pyvts):
    """测试成功断开 VTS 连接"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.close = AsyncMock()
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = True  # 模拟已连接
    adapter.vts = mock_vts_instance

    result = await adapter.disconnect()

    assert result is True
    assert adapter.is_connected is False
    mock_vts_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_not_connected(vts_config, mock_pyvts):
    """测试未连接时断开"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter.vts = None

    result = await adapter.disconnect()

    # 应该返回 False（没有连接可断开）
    assert result is False


@pytest.mark.asyncio
async def test_disconnect_exception(vts_config, mock_pyvts):
    """测试断开时抛出异常"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.close = AsyncMock(side_effect=Exception("断开失败"))
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = True
    adapter.vts = mock_vts_instance

    result = await adapter.disconnect()

    assert result is False


# =============================================================================
# set_parameters() 测试
# =============================================================================


@pytest.mark.asyncio
async def test_set_parameters_success(vts_config, mock_pyvts):
    """测试成功设置参数"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.vts_request = Mock()
    mock_vts_instance.vts_request.requestSetParameterValue = Mock(
        return_value={"messageType": "InjectParameterDataResponse"}
    )
    mock_vts_instance.request = AsyncMock(return_value={"messageType": "InjectParameterDataResponse"})
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = True
    adapter.vts = mock_vts_instance

    params = {"smile": 0.8, "eye_open": 0.9}
    result = await adapter.set_parameters(params)

    assert result is True

    # 验证每个参数都被设置
    assert mock_vts_instance.request.call_count == 3  # MouthSmile + EyeOpenLeft + EyeOpenRight


@pytest.mark.asyncio
async def test_set_parameters_not_connected(vts_config, mock_pyvts):
    """测试未连接时设置参数"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = False  # 未连接

    params = {"smile": 0.8}
    result = await adapter.set_parameters(params)

    assert result is False


@pytest.mark.asyncio
async def test_set_parameters_failure_response(vts_config, mock_pyvts):
    """测试设置参数失败（错误响应）"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.vts_request = Mock()
    mock_vts_instance.vts_request.requestSetParameterValue = Mock(return_value={"messageType": "ErrorResponse"})
    mock_vts_instance.request = AsyncMock(return_value={"messageType": "ErrorResponse"})
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = True
    adapter.vts = mock_vts_instance

    params = {"smile": 0.8}
    result = await adapter.set_parameters(params)

    assert result is False


@pytest.mark.asyncio
async def test_set_parameters_exception(vts_config, mock_pyvts):
    """测试设置参数时抛出异常"""
    # Mock pyvts
    mock_vts_instance = AsyncMock()
    mock_vts_instance.request = AsyncMock(side_effect=Exception("设置失败"))
    mock_pyvts.vts = Mock(return_value=mock_vts_instance)

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)
    adapter._is_connected = True
    adapter.vts = mock_vts_instance

    params = {"smile": 0.8}
    result = await adapter.set_parameters(params)

    assert result is False


# =============================================================================
# 边界条件测试
# =============================================================================


def test_param_translation_mapping(vts_config, mock_pyvts):
    """测试参数翻译映射表完整性"""
    mock_pyvts.vts = Mock

    from src.domains.output.adapters.vts.vts_adapter import VTSAdapter

    adapter = VTSAdapter(vts_config)

    # 验证映射表包含预期的键
    assert "smile" in VTSAdapter.PARAM_TRANSLATION
    assert "eye_open" in VTSAdapter.PARAM_TRANSLATION
    assert "mouth_open" in VTSAdapter.PARAM_TRANSLATION
    assert "brow_down" in VTSAdapter.PARAM_TRANSLATION

    # 验证映射值
    assert VTSAdapter.PARAM_TRANSLATION["smile"] == "MouthSmile"
    assert VTSAdapter.PARAM_TRANSLATION["eye_open"] == "EyeOpenLeft"
    assert VTSAdapter.PARAM_TRANSLATION["mouth_open"] == "MouthOpen"
    assert VTSAdapter.PARAM_TRANSLATION["brow_down"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
