"""GPTSoVITSClient（api_v2 方言）：参考音频、参数构造、权重切换、同步/流式合成、连接错误转译、探活"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.modules.tts.backends.gptsovits.client import GPTSoVITSClient, GPTSoVITSServiceError


@pytest.fixture
def client() -> GPTSoVITSClient:
    return GPTSoVITSClient(host="127.0.0.1", port=9880)


@pytest.fixture
def get():
    """替换 requests.get；用例自己设置 return_value / side_effect。"""
    with patch("src.modules.tts.backends.gptsovits.client.requests.get") as mock_get:
        yield mock_get


def _response(status: int = 200, **attrs) -> MagicMock:
    return MagicMock(status_code=status, **attrs)


def test_init_initialize_and_reference_audio_validation(client: GPTSoVITSClient) -> None:
    assert (client.host, client.port, client.base_url) == ("127.0.0.1", 9880, "http://127.0.0.1:9880")
    assert client._ref_audio_path is None and client._prompt_text == "" and not client._initialized

    client.initialize()
    client.initialize()  # 重复初始化不抛
    assert client._initialized
    client.load_preset("default")
    assert client._current_preset == "default"

    client.set_refer_audio("/path/to/audio.wav", "测试文本")
    assert (client._ref_audio_path, client._prompt_text) == ("/path/to/audio.wav", "测试文本")
    with pytest.raises(ValueError, match="audio_path 不能为空"):
        client.set_refer_audio("", "测试文本")
    with pytest.raises(ValueError, match="prompt_text 不能为空"):
        client.set_refer_audio("/path/to/audio.wav", "")


@pytest.mark.parametrize(
    ("setter", "route", "failure"),
    [
        ("set_gpt_weights", "set_gpt_weights", "设置 GPT 权重失败"),
        ("set_sovits_weights", "set_sovits_weights", "设置 SoVITS 权重失败"),
    ],
)
def test_set_weights_success_and_failure(client: GPTSoVITSClient, get, setter: str, route: str, failure: str) -> None:
    get.return_value = _response(200)
    getattr(client, setter)("/path/to/weights.pth")
    assert route in get.call_args[0][0]

    get.return_value = _response(500, json=MagicMock(return_value={"message": "设置失败"}))
    with pytest.raises(Exception, match=failure):
        getattr(client, setter)("/path/to/weights.pth")


@pytest.mark.parametrize(
    ("text", "language", "expected"), [("你好世界", "auto", "zh"), ("Hello World", "auto", "en"), ("你好", "zh", "zh")]
)
def test_detect_language(client: GPTSoVITSClient, text: str, language: str, expected: str) -> None:
    assert client._detect_language(text, language) == expected


def test_build_params_uses_api_v2_dialect_and_omits_unset_sampling_args(client: GPTSoVITSClient) -> None:
    with pytest.raises(ValueError, match="ref_audio_path 不能为空"):  # api_v2 参考音频每请求必填
        client._build_params(text="测试文本")

    client.set_refer_audio("/path/to/audio.wav", "测试文本")
    params = client._build_params(
        text="测试文本",
        text_lang="zh",
        prompt_lang="zh",
        top_k=20,
        top_p=0.6,
        temperature=0.3,
        speed_factor=1.25,
        streaming_mode=True,
        media_type="wav",
    )
    assert (params["text"], params["text_lang"], params["prompt_lang"]) == ("测试文本", "zh", "zh")
    assert (params["ref_audio_path"], params["prompt_text"]) == ("/path/to/audio.wav", "测试文本")
    assert (params["streaming_mode"], params["media_type"]) == (1, "wav")
    assert (params["top_k"], params["top_p"], params["temperature"], params["speed_factor"]) == (20, 0.6, 0.3, 1.25)

    bare = client._build_params(text="测试文本", text_lang="zh", prompt_lang="zh")
    assert not {"top_k", "top_p", "temperature", "speed_factor", "sample_steps", "super_sampling"} & set(bare)
    for level in (0, 1, 2, 3):  # 流式档位原样透传
        assert (
            client._build_params(text="测", text_lang="zh", prompt_lang="zh", streaming_mode=level)["streaming_mode"]
            == level
        )


def test_set_refer_audio_remote(client: GPTSoVITSClient, get) -> None:
    get.return_value = _response(200)
    client.set_refer_audio_remote("referenceAudio/maimai.wav")
    assert "set_refer_audio" in get.call_args[0][0]
    assert get.call_args[1]["params"] == {"refer_audio_path": "referenceAudio/maimai.wav"}
    assert client._ref_audio_path == "referenceAudio/maimai.wav"  # 本地同步记录路径

    get.return_value = _response(400, json=MagicMock(return_value={"message": "set refer audio failed"}))
    with pytest.raises(Exception, match="远程设置参考音频失败"):
        client.set_refer_audio_remote("bad/path.wav")


def test_tts_and_tts_stream_success_and_http_failure(client: GPTSoVITSClient, get) -> None:
    client.set_refer_audio("/path/to/audio.wav", "测试文本")

    get.return_value = _response(200, content=b"fake_audio_data", iter_content=MagicMock(return_value=[b"c1", b"c2"]))
    assert client.tts(text="测试文本", text_lang="zh") == b"fake_audio_data"
    assert list(client.tts_stream(text="测试文本", text_lang="zh")) == [b"c1", b"c2"]

    get.return_value = _response(500, json=MagicMock(return_value={"message": "失败"}))
    with pytest.raises(Exception, match="TTS 请求失败"):
        client.tts(text="测试文本", text_lang="zh")
    with pytest.raises(Exception, match="流式 TTS 请求失败"):
        list(client.tts_stream(text="测试文本", text_lang="zh"))


def test_transport_errors_are_translated_to_short_service_errors(client: GPTSoVITSClient, get) -> None:
    client.set_refer_audio("ref.wav", "参考文本")
    get.side_effect = requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='127.0.0.1', port=9880): Max retries exceeded with url: /?text=xxx"
    )
    with pytest.raises(GPTSoVITSServiceError) as exc_info:
        client.tts_stream(text="测试文本", text_lang="zh")
    message = str(exc_info.value)
    assert "服务不可达" in message and "http://127.0.0.1:9880" in message
    assert "HTTPConnectionPool" not in message and "Max retries" not in message  # 不携带底层噪声
    assert isinstance(exc_info.value.__cause__, requests.exceptions.ConnectionError)  # 原始异常保留供调试

    with pytest.raises(GPTSoVITSServiceError, match="服务不可达"):  # 同步 TTS 同样转译
        client.tts(text="测试文本", text_lang="zh")

    get.side_effect = requests.exceptions.Timeout("timed out")
    with pytest.raises(GPTSoVITSServiceError, match="请求超时"):
        client.tts_stream(text="测试文本", text_lang="zh")


def test_check_connection_treats_any_http_response_as_reachable(client: GPTSoVITSClient, get) -> None:
    get.return_value = _response(200)
    assert client.check_connection() is True
    assert "/tts" in get.call_args[0][0]  # api_v2 无根路由，探活打 /tts

    for status in (400, 422, 500):  # 无参 /tts 被应用层拒绝：收到响应即服务可达
        get.return_value = _response(status)
        assert client.check_connection() is True, f"HTTP {status} 应判可达"

    get.side_effect = Exception("连接失败")
    assert client.check_connection() is False
