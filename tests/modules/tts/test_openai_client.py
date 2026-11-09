"""OpenAITTSClient 特有行为：请求组装、音色形态、错误响应提取、音频流透传

六个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from src.modules.tts.backends.openai.client import OpenAITTSApiError, OpenAITTSClient


def make_client(**overrides: Any) -> OpenAITTSClient:
    params: dict[str, Any] = {
        "base_url": "https://api.openai.com/v1/audio/speech",
        "api_key": "sk-test",
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "instructions": "用轻快的语气说话",
        "speed": 1.1,
    }
    params.update(overrides)
    return OpenAITTSClient(**params)


# ----- 请求组装 -----


def test_payload_carries_speech_endpoint_shape() -> None:
    payload = make_client()._build_payload("你好")
    assert payload["model"] == "gpt-4o-mini-tts"
    assert payload["input"] == "你好"
    assert payload["voice"] == "alloy"
    assert payload["response_format"] == "pcm"  # 裸 PCM：逐块即播，无需容器解析
    assert payload["speed"] == 1.1
    assert payload["instructions"] == "用轻快的语气说话"


def test_payload_omits_empty_instructions() -> None:
    """空风格提示不携带：该字段对 tts-1 系列无效，发空串只会被静默忽略。"""
    assert "instructions" not in make_client(instructions="  ")._build_payload("你好")


def test_headers_carry_bearer_token() -> None:
    headers = make_client()._build_headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"


# ----- 错误响应提取 -----


def test_error_detail_extracts_openai_error_message() -> None:
    body = json.dumps({"error": {"message": "Invalid voice 'nope'.", "type": "invalid_request_error"}})
    assert OpenAITTSClient._error_detail(body) == "Invalid voice 'nope'."


def test_error_detail_falls_back_to_raw_body_when_not_json() -> None:
    """网关返回 HTML 或其他非 JSON 时原样截断保留，不能因解析失败再抛一次。"""
    assert OpenAITTSClient._error_detail("<html>502 Bad Gateway</html>") == "<html>502 Bad Gateway</html>"


def test_error_detail_falls_back_when_json_shape_is_unexpected() -> None:
    assert OpenAITTSClient._error_detail(json.dumps({"detail": "boom"})) == '{"detail": "boom"}'


# ----- 音频流 -----


class _FakeContent:
    """aiohttp 响应体替身：iter_any 按给定字节序列吐块。"""

    def __init__(self, *pieces: bytes) -> None:
        self._pieces = list(pieces)

    async def iter_any(self) -> AsyncIterator[bytes]:
        for piece in self._pieces:
            yield piece


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"", error_text: str = "") -> None:
        self.status = status
        self.content = _FakeContent(body) if status == 200 else _FakeContent()
        self._error_text = error_text

    async def text(self) -> str:
        return self._error_text


class _FakeContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeSession:
    closed = False

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.posted: dict[str, Any] = {}

    def post(self, url: str, **kwargs: Any) -> _FakeContext:
        self.posted = {"url": url, **kwargs}
        return _FakeContext(self._response)


async def _collect(client: OpenAITTSClient, text: str = "你好") -> list[bytes]:
    return [chunk async for chunk in client.stream_synthesize(text)]


async def test_stream_synthesize_yields_response_chunks_verbatim() -> None:
    """响应体是裸 PCM 字节流：收到多少交多少，不重组、不解析。"""
    session = _FakeSession(_FakeResponse(200, b"\x01\x02\x03\x04"))
    client = make_client()
    client._session = session  # type: ignore[assignment]

    assert await _collect(client) == [b"\x01\x02\x03\x04"]
    assert session.posted["url"] == "https://api.openai.com/v1/audio/speech"
    assert session.posted["json"]["input"] == "你好"


async def test_stream_synthesize_raises_with_server_message_on_http_error() -> None:
    """HTTP 非 200 抛简短业务异常，携带服务端错误描述（便于直接看懂原因）。"""
    body = json.dumps({"error": {"message": "Incorrect API key provided."}})
    client = make_client()
    client._session = _FakeSession(_FakeResponse(401, error_text=body))  # type: ignore[assignment]

    with pytest.raises(OpenAITTSApiError, match="HTTP 401.*Incorrect API key"):
        await _collect(client)
