"""
STTInputProvider 测试
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def stt_config():
    return {
        "iflytek_asr": {
            "host": "wss://iat-api.xfyun.cn",
            "path": "/v2/iat",
            "appid": "test_appid",
            "api_secret": "test_secret",
            "api_key": "test_key",
            "language": "zh_cn",
            "domain": "iat",
        },
        "vad": {
            "enable": False,
            "vad_threshold": 0.5,
            "silence_seconds": 1.0,
        },
        "audio": {
            "sample_rate": 16000,
            "channels": 1,
            "dtype": "int16",
            "use_remote_stream": True,
        },
        "message_config": {
            "user_id": "stt_user",
            "user_nickname": "语音",
        },
    }


class TestSTTInputProvider:
    """测试 STTInputProvider（使用 mock 避免依赖问题）"""

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_init_with_custom_config(self, mock_deps, stt_config):
        """测试自定义配置初始化"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        assert provider.sample_rate == 16000
        assert provider.use_remote_stream is True
        assert provider.vad_enabled is False

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_sample_rate_validation(self, mock_deps, stt_config):
        """测试采样率验证"""
        from src.domains.input.providers.stt import STTInputProvider

        config = stt_config.copy()
        config["audio"]["sample_rate"] = 22050
        provider = STTInputProvider(config)

        assert provider.sample_rate == 16000

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_block_size_calculation(self, mock_deps, stt_config):
        """测试块大小计算"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        assert provider.block_size_samples == 512
        assert provider.block_size_ms == 32

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_block_size_for_8khz(self, mock_deps, stt_config):
        """测试 8kHz 的块大小"""
        from src.domains.input.providers.stt import STTInputProvider

        config = stt_config.copy()
        config["audio"]["sample_rate"] = 8000
        provider = STTInputProvider(config)

        assert provider.block_size_samples == 256

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_iflytek_auth_url_building(self, mock_deps, stt_config):
        """测试讯飞认证 URL 构建"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        url = provider._build_iflytek_auth_url()

        assert url.startswith("wss://")
        assert "authorization=" in url
        assert "date=" in url

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_iflytek_common_params(self, mock_deps, stt_config):
        """测试讯飞公共参数构建"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        params = provider._build_iflytek_common_params()

        assert params["app_id"] == "test_appid"

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_iflytek_business_params(self, mock_deps, stt_config):
        """测试讯飞业务参数构建"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        params = provider._build_iflytek_business_params()

        assert params["language"] == "zh_cn"
        assert params["domain"] == "iat"

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_iflytek_data_params(self, mock_deps, stt_config):
        """测试讯飞数据参数构建"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        params = provider._build_iflytek_data_params(0, b"test_audio")

        assert params["status"] == 0
        assert params["format"] == "audio/L16;rate=16000"

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    def test_iflytek_frame_building(self, mock_deps, stt_config):
        """测试讯飞帧构建"""
        import json

        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        frame = provider._build_iflytek_frame(0, b"audio_data")

        frame_dict = json.loads(frame.decode("utf-8"))

        assert "common" in frame_dict
        assert "business" in frame_dict
        assert "data" in frame_dict

    @patch("src.domains.input.providers.stt.stt_input_provider.STTInputProvider._check_dependencies")
    @pytest.mark.asyncio
    async def test_cleanup(self, mock_deps, stt_config):
        """测试资源清理"""
        from src.domains.input.providers.stt import STTInputProvider

        provider = STTInputProvider(stt_config)

        # 创建 mock session
        mock_session = AsyncMock()
        mock_session.closed = False
        provider._session = mock_session

        await provider._cleanup()

        # 验证 close 被调用
        mock_session.close.assert_called_once()

    def test_get_registration_info(self):
        """测试注册信息"""
        from src.domains.input.providers.stt import STTInputProvider

        info = STTInputProvider.get_registration_info()

        assert info["layer"] == "input"
        assert info["name"] == "stt"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
