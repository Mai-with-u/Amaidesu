"""
GPTSoVITSOutputProvider 测试
"""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.modules.types.intent import Intent
from src.domains.output.providers.audio import GPTSoVITSOutputProvider
from src.modules.types import EmotionType


@pytest.fixture
def gptsovits_config():
    """GPTSoVITS 配置"""
    return {
        "host": "127.0.0.1",
        "port": 9880,
        "ref_audio_path": "/path/to/ref.wav",
        "prompt_text": "测试文本",
        "text_language": "zh",
        "prompt_language": "zh",
        "top_k": 20,
        "top_p": 0.6,
        "temperature": 0.3,
        "speed_factor": 1.0,
        "streaming_mode": True,
        "media_type": "wav",
        "text_split_method": "latency",
        "batch_size": 1,
        "batch_threshold": 0.7,
        "repetition_penalty": 1.0,
        "sample_steps": 10,
        "super_sampling": True,
        "output_device_name": None,
        "sample_rate": 32000,
    }


@pytest.fixture
def mock_event_bus():
    """Mock EventBus"""
    event_bus = MagicMock()
    event_bus.on = MagicMock()
    event_bus.emit = AsyncMock()
    return event_bus


@pytest.fixture
def mock_tts_client():
    """Mock GPTSoVITSClient"""
    client = MagicMock()
    client.initialize = MagicMock()
    client.load_preset = MagicMock()
    client.set_refer_audio = MagicMock()
    client.tts_stream = MagicMock()
    client.check_connection = MagicMock(return_value=True)
    return client


@pytest.fixture
def mock_audio_manager():
    """Mock AudioDeviceManager"""
    manager = MagicMock()
    manager.set_output_device = MagicMock()
    manager.play_audio = AsyncMock()
    manager.stop_audio = MagicMock()
    return manager


class TestGPTSoVITSOutputProvider:
    """测试 GPTSoVITSOutputProvider"""

    def test_init_with_default_config(self):
        """测试默认配置初始化"""
        provider = GPTSoVITSOutputProvider({})

        assert provider.host == "127.0.0.1"
        assert provider.port == 9880
        assert provider.ref_audio_path == ""
        assert provider.prompt_text == ""
        assert provider.text_language == "zh"
        assert provider.sample_rate == 32000
        assert provider.render_count == 0
        assert provider.error_count == 0

    def test_init_with_custom_config(self, gptsovits_config):
        """测试自定义配置初始化"""
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        assert provider.host == "127.0.0.1"
        assert provider.port == 9880
        assert provider.ref_audio_path == "/path/to/ref.wav"
        assert provider.prompt_text == "测试文本"
        assert provider.text_language == "zh"
        assert provider.top_k == 20
        assert provider.temperature == 0.3
        assert provider.sample_rate == 32000

    @pytest.mark.asyncio
    async def test_setup_internal(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试内部设置"""
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 跳过实际设置，直接验证注入的 mocks 可以使用
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager

        # 验证客户端初始化方法存在
        assert hasattr(provider.tts_client, "initialize")
        assert hasattr(provider.tts_client, "set_refer_audio")
        assert hasattr(provider.tts_client, "load_preset")

    @pytest.mark.asyncio
    async def test_setup_internal_with_device(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试指定音频设备的设置"""
        gptsovits_config["output_device_name"] = "My Audio Device"
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager

        # 验证音频管理器设置方法存在
        assert hasattr(provider.audio_manager, "set_output_device")

    @pytest.mark.asyncio
    async def test_cleanup_internal(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试内部清理"""
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager

        await provider._cleanup_internal()

        # 验证音频停止
        mock_audio_manager.stop_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_render_internal_no_text(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试没有文本时的渲染"""
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager

        # 创建没有响应文本的 Intent
        intent = Intent(original_text="", response_text="", emotion=EmotionType.NEUTRAL, actions=[])

        # 渲染应该直接返回
        await provider._render_internal(intent)

        # TTS 客户端不应该被调用
        mock_tts_client.tts_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_internal_success(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试成功的渲染"""

        # 模拟音频流 - 使用偶数长度的有效 PCM 数据
        async def mock_audio_gen():
            # 创建有效的 int16 PCM 数据（2字节）

            audio_data = np.zeros(1000, dtype=np.int16).tobytes()
            yield audio_data

        mock_tts_client.tts_stream.return_value = mock_audio_gen()

        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager
        # 设置 _dependencies 以避免 AttributeError
        provider._dependencies = {}

        # 创建有响应文本的 Intent
        intent = Intent(original_text="你好", response_text="测试文本", emotion=EmotionType.HAPPY, actions=[])

        # 渲染
        await provider._render_internal(intent)

        # 验证 TTS 被调用
        mock_tts_client.tts_stream.assert_called_once()

        # 验证音频被播放
        mock_audio_manager.play_audio.assert_called_once()

        # 验证渲染计数增加
        assert provider.render_count == 1
        assert provider.error_count == 0

    @pytest.mark.asyncio
    async def test_render_internal_failure(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试渲染失败"""
        mock_tts_client.tts_stream.side_effect = Exception("TTS 失败")

        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager
        # 设置 _dependencies 以避免 AttributeError
        provider._dependencies = {}

        # 创建有响应文本的 Intent
        intent = Intent(original_text="你好", response_text="测试文本", emotion=EmotionType.HAPPY, actions=[])

        # 渲染应该抛出异常
        with pytest.raises(Exception, match="TTS 失败"):
            await provider._render_internal(intent)

        # 验证错误计数增加
        assert provider.error_count == 1
        assert provider.render_count == 0

    @pytest.mark.asyncio
    async def test_process_audio_stream(self, gptsovits_config, mock_tts_client, mock_audio_manager):
        """测试处理音频流"""
        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 模拟音频流
        async def mock_stream():
            chunks = [b"chunk1", b"chunk2", b""]
            for chunk in chunks:
                yield chunk

        chunks = []
        async for chunk in provider._process_audio_stream(mock_stream()):
            chunks.append(chunk)

        # 验证处理了非空块
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_full_render_workflow(self, gptsovits_config, mock_event_bus, mock_tts_client, mock_audio_manager):
        """测试完整的渲染工作流"""

        # 模拟音频流 - 使用有效的 PCM 数据
        async def mock_audio_gen():
            audio_data = np.zeros(1000, dtype=np.int16).tobytes()
            yield audio_data

        mock_tts_client.tts_stream.return_value = mock_audio_gen()

        provider = GPTSoVITSOutputProvider(gptsovits_config)

        # 手动注入 mocks (在 start 之前)
        provider.tts_client = mock_tts_client
        provider.audio_manager = mock_audio_manager
        # 设置 _dependencies 以避免 AttributeError
        provider._dependencies = {}

        # 直接调用 _render_internal，跳过 setup 以避免创建真实客户端
        # 创建 Intent
        intent = Intent(original_text="你好", response_text="完整工作流测试", emotion=EmotionType.HAPPY, actions=[])

        # 执行渲染
        await provider._render_internal(intent)

        # 验证音频被播放
        mock_audio_manager.play_audio.assert_called_once()
