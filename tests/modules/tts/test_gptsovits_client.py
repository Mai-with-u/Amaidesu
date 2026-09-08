"""
GPTSoVITSClient 测试
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.modules.tts.gptsovits_client import GPTSoVITSClient, GPTSoVITSServiceError


@pytest.fixture
def client():
    """创建客户端实例"""
    return GPTSoVITSClient(host="127.0.0.1", port=9880)


class TestGPTSoVITSClient:
    """测试 GPTSoVITSClient"""

    def test_init(self, client):
        """测试初始化"""
        assert client.host == "127.0.0.1"
        assert client.port == 9880
        assert client.base_url == "http://127.0.0.1:9880"
        assert client._ref_audio_path is None
        assert client._prompt_text == ""
        assert not client._initialized

    def test_initialize(self, client):
        """测试初始化方法"""
        client.initialize()
        assert client._initialized

    def test_initialize_twice(self, client):
        """测试重复初始化"""
        client.initialize()
        client.initialize()  # 不应该抛出异常
        assert client._initialized

    def test_load_preset(self, client):
        """测试加载预设"""
        client.initialize()
        client.load_preset("default")
        assert client._current_preset == "default"

    def test_set_refer_audio(self, client):
        """测试设置参考音频"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")
        assert client._ref_audio_path == "/path/to/audio.wav"
        assert client._prompt_text == "测试文本"

    def test_set_refer_audio_empty_audio_path(self, client):
        """测试设置空音频路径"""
        with pytest.raises(ValueError, match="audio_path 不能为空"):
            client.set_refer_audio("", "测试文本")

    def test_set_refer_audio_empty_prompt_text(self, client):
        """测试设置空的提示文本"""
        with pytest.raises(ValueError, match="prompt_text 不能为空"):
            client.set_refer_audio("/path/to/audio.wav", "")

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_set_gpt_weights_success(self, mock_get, client):
        """测试设置 GPT 权重成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        client.set_gpt_weights("/path/to/weights.pth")

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "set_gpt_weights" in call_args[0][0]

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_set_gpt_weights_failure(self, mock_get, client):
        """测试设置 GPT 权重失败"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "设置失败"}
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="设置 GPT 权重失败"):
            client.set_gpt_weights("/path/to/weights.pth")

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_set_sovits_weights_success(self, mock_get, client):
        """测试设置 SoVITS 权重成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        client.set_sovits_weights("/path/to/weights.pth")

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "set_sovits_weights" in call_args[0][0]

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_set_sovits_weights_failure(self, mock_get, client):
        """测试设置 SoVITS 权重失败"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "设置失败"}
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="设置 SoVITS 权重失败"):
            client.set_sovits_weights("/path/to/weights.pth")

    def test_detect_language_auto_chinese(self, client):
        """测试自动检测中文"""
        result = client._detect_language("你好世界", "auto")
        assert result == "zh"

    def test_detect_language_auto_english(self, client):
        """测试自动检测英文"""
        result = client._detect_language("Hello World", "auto")
        assert result == "en"

    def test_detect_language_manual(self, client):
        """测试手动指定语言"""
        result = client._detect_language("你好", "zh")
        assert result == "zh"

    def test_build_params_success(self, client):
        """测试构建参数成功"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")

        params = client._build_params(
            text="测试文本",
            text_lang="zh",
            prompt_lang="zh",
        )

        assert params["text"] == "测试文本"
        assert params["text_language"] == "zh"
        assert params["refer_wav_path"] == "/path/to/audio.wav"
        assert params["prompt_text"] == "测试文本"
        assert params["prompt_language"] == "zh"

    def test_build_params_no_ref_audio(self, client):
        """未设置参考音频：参数省略参考音频三键（服务端用默认）"""
        params = client._build_params(text="测试文本")
        assert "refer_wav_path" not in params
        assert "prompt_text" not in params
        assert "prompt_language" not in params
        assert params["text"] == "测试文本"

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_success(self, mock_get, client):
        """测试同步 TTS 成功"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake_audio_data"
        mock_get.return_value = mock_response

        result = client.tts(text="测试文本", text_lang="zh")

        assert result == b"fake_audio_data"

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_failure(self, mock_get, client):
        """测试同步 TTS 失败"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "TTS 失败"}
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="TTS 请求失败"):
            client.tts(text="测试文本", text_lang="zh")

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_stream_success(self, mock_get, client):
        """测试流式 TTS 成功"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_get.return_value = mock_response

        result = client.tts_stream(text="测试文本", text_lang="zh")

        chunks = list(result)
        assert chunks == [b"chunk1", b"chunk2"]

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_stream_failure(self, mock_get, client):
        """测试流式 TTS 失败"""
        client.set_refer_audio("/path/to/audio.wav", "测试文本")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "流式 TTS 失败"}
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="流式 TTS 请求失败"):
            result = client.tts_stream(text="测试文本", text_lang="zh")
            list(result)  # 触发迭代器

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_stream_connection_error_translated(self, mock_get, client):
        """服务未启动：连接错误转译为简短 GPTSoVITSServiceError，不携带底层噪声"""
        mock_get.side_effect = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=9880): Max retries exceeded "
            "with url: /?text=xxx (Caused by NewConnectionError(...))"
        )

        with pytest.raises(GPTSoVITSServiceError) as exc_info:
            client.tts_stream(text="测试文本", text_lang="zh")

        message = str(exc_info.value)
        assert "服务不可达" in message
        assert "http://127.0.0.1:9880" in message
        assert "HTTPConnectionPool" not in message
        assert "Max retries" not in message
        # 原始异常保留在 __cause__ 供调试
        assert isinstance(exc_info.value.__cause__, requests.exceptions.ConnectionError)

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_connection_error_translated(self, mock_get, client):
        """同步 TTS：连接错误同样转译为简短业务异常"""
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        with pytest.raises(GPTSoVITSServiceError, match="服务不可达"):
            client.tts(text="测试文本", text_lang="zh")

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_tts_stream_timeout_translated(self, mock_get, client):
        """超时转译为简短业务异常"""
        mock_get.side_effect = requests.exceptions.Timeout("timed out")

        with pytest.raises(GPTSoVITSServiceError, match="请求超时"):
            client.tts_stream(text="测试文本", text_lang="zh")

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_check_connection_success(self, mock_get, client):
        """测试检查连接成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = client.check_connection()
        assert result is True

    @patch("src.modules.tts.gptsovits_client.requests.get")
    def test_check_connection_failure(self, mock_get, client):
        """测试检查连接失败"""
        mock_get.side_effect = Exception("连接失败")

        result = client.check_connection()
        assert result is False
