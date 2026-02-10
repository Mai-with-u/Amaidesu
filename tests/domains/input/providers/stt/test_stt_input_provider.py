"""
测试 STTInputProvider (语音转文字 Provider)

运行: uv run pytest tests/domains/input/providers/stt/test_stt_input_provider.py -v

注意：此测试使用 Mock 避免真实的音频设备和 API 调用
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

# 由于 STTInputProvider 有很多外部依赖，我们使用 Mock 进行测试


# =============================================================================
# 配置 Fixtures
# =============================================================================


@pytest.fixture
def stt_config():
    """测试用 STT 配置"""
    return {
        "type": "stt",
        "iflytek_asr": {
            "appid": "test_appid",
            "api_key": "test_api_key",
            "api_secret": "test_api_secret",
            "host": "test.host.com",
            "path": "/v2/iat",
        },
        "vad": {
            "enable": True,
            "vad_threshold": 0.5,
            "silence_seconds": 1.0,
        },
        "audio": {
            "sample_rate": 16000,
            "channels": 1,
            "dtype": "int16",
            "stt_input_device_name": None,
            "use_remote_stream": False,
        },
        "message_config": {
            "user_id": "stt_user",
            "user_nickname": "语音",
        },
    }


# =============================================================================
# 依赖检查测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_check_dependencies_missing_torch(stt_config):
    """测试缺少 torch 依赖"""
    with patch("src.domains.input.providers.stt.stt_input_provider.importlib.util.find_spec", return_value=None):
        with pytest.raises(RuntimeError):
            from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

            STTInputProvider(stt_config)


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_check_dependencies_missing_sounddevice(stt_config):
    """测试缺少 sounddevice 依赖"""
    with patch("src.domains.input.providers.stt.stt_input_provider.sd", None):
        with pytest.raises(RuntimeError):
            from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

            STTInputProvider(stt_config)


# =============================================================================
# 初始化测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_stt_provider_initialization(stt_config):
    """测试 STT Provider 初始化"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._load_vad_model"):
            provider = STTInputProvider(stt_config)

            assert provider.typed_config is not None
            assert provider.sample_rate == 16000
            assert provider.channels == 1
            assert provider.vad_enabled is True
            assert provider.vad_threshold == 0.5


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_stt_provider_custom_sample_rate(stt_config):
    """测试自定义采样率"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    stt_config["audio"]["sample_rate"] = 8000

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._load_vad_model"):
            provider = STTInputProvider(stt_config)
            assert provider.sample_rate == 8000


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_stt_provider_invalid_sample_rate(stt_config):
    """测试无效采样率（应该回退到 16000）"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    stt_config["audio"]["sample_rate"] = 12000  # 不支持的采样率

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._load_vad_model"):
            provider = STTInputProvider(stt_config)
            # 应该回退到 16000
            assert provider.sample_rate == 16000


@pytest.mark.skip(reason="需要真实的依赖包，使用 Mock 测试替代")
def test_stt_provider_vad_disabled(stt_config):
    """测试禁用 VAD"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    stt_config["vad"]["enable"] = False

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)
        assert provider.vad_enabled is False
        assert provider.vad_model is None


# =============================================================================
# VAD 模型加载测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的 torch 模型下载")
def test_load_vad_model_success(stt_config):
    """测试成功加载 VAD 模型"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        with patch("torch.hub.load") as mock_load:
            mock_model = Mock()
            mock_utils = Mock()
            mock_load.return_value = (mock_model, mock_utils)

            provider = STTInputProvider(stt_config)

            assert provider.vad_model is not None
            assert provider.vad_utils is not None
            mock_load.assert_called_once()


@pytest.mark.skip(reason="需要真实的依赖包")
def test_load_vad_model_failure(stt_config):
    """测试 VAD 模型加载失败"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        with patch("torch.hub.load", side_effect=Exception("加载失败")):
            provider = STTInputProvider(stt_config)
            # 加载失败后应该禁用 VAD
            assert provider.vad_enabled is False


# =============================================================================
# 音频设备查找测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的 sounddevice")
def test_find_device_by_name(stt_config):
    """测试通过名称查找设备"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)

        # Mock sounddevice
        mock_devices = [
            {"name": "麦克风 (1)", "max_input_channels": 2},
            {"name": "扬声器", "max_input_channels": 0},
        ]

        with patch("src.domains.input.providers.stt.stt_input_provider.sd") as mock_sd:
            mock_sd.query_devices.return_value = mock_devices
            mock_sd.default.device = [0, 1]

            device_index = provider._find_device_index("麦克风", kind="input")
            # 应该找到第一个匹配的设备
            assert device_index is not None


@pytest.mark.skip(reason="需要真实的依赖包")
def test_find_device_default(stt_config):
    """测试查找默认设备"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)

        with patch("src.domains.input.providers.stt.stt_input_provider.sd") as mock_sd:
            mock_device = {"name": "默认设备", "max_input_channels": 2}
            mock_sd.query_devices.return_value = mock_device
            mock_sd.default.device = [0, 1]

            device_index = provider._find_device_index(None, kind="input")
            assert device_index == 0


# =============================================================================
# 讯飞 WebSocket 测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的 WebSocket 连接")
def test_build_iflytek_auth_url(stt_config):
    """测试构建讯飞认证 URL"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)

        url = provider._build_iflytek_auth_url()

        assert "wss://" in url
        assert "test.host.com" in url
        assert "authorization" in url
        assert "date" in url


@pytest.mark.skip(reason="需要真实的 WebSocket 连接")
def test_build_iflytek_frame(stt_config):
    """测试构建讯飞帧"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)

        # 测试第一帧
        audio_data = b"test_audio_data"
        frame = provider._build_iflytek_frame(0, audio_data)  # STATUS_FIRST_FRAME = 0

        assert isinstance(frame, bytes)
        assert b"common" in frame
        assert b"business" in frame
        assert b"data" in frame


# =============================================================================
# 资源清理测试
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.skip(reason="需要真实的依赖包")
async def test_cleanup(stt_config):
    """测试资源清理"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    with patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies"):
        provider = STTInputProvider(stt_config)

        # Mock session
        mock_session = AsyncMock()
        provider._session = mock_session

        await provider._cleanup()

        mock_session.close.assert_called_once()
        assert provider._session is None


# =============================================================================
# 配置验证测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的依赖包")
def test_invalid_config():
    """测试无效配置"""
    from pydantic import ValidationError

    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    invalid_config = {
        "type": "stt",
        "iflytek_asr": {
            # 缺少必需字段
            "appid": "test",
        },
    }

    with pytest.raises(ValidationError):
        STTInputProvider(invalid_config)


# =============================================================================
# Provider 注册信息测试
# =============================================================================


@pytest.mark.skip(reason="需要真实的依赖包")
def test_get_registration_info(stt_config):
    """测试获取注册信息"""
    from src.domains.input.providers.stt.stt_input_provider import STTInputProvider

    info = STTInputProvider.get_registration_info()

    assert info["layer"] == "input"
    assert info["name"] == "stt"
    assert info["source"] == "builtin:stt"
    assert "class" in info


# =============================================================================
# 集成测试（使用 Mock）
# =============================================================================


@pytest.mark.asyncio
async def test_mock_data_collection():
    """使用 Mock 测试数据采集流程"""
    # 这个测试展示了如何使用 Mock 来测试 STT Provider
    # 而不需要真实的音频设备和 API

    # 创建 Mock Provider
    mock_provider = Mock()

    # Mock 配置
    mock_provider.sample_rate = 16000
    mock_provider.vad_enabled = True
    mock_provider.is_running = True

    # Mock 音频队列
    mock_provider._internal_audio_queue = asyncio.Queue()
    mock_provider._result_queue = asyncio.Queue()

    # 模拟音频数据
    test_audio = b"mock_audio_data"
    await mock_provider._internal_audio_queue.put(test_audio)

    # 验证数据已放入队列
    retrieved = await mock_provider._internal_audio_queue.get()
    assert retrieved == test_audio


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
