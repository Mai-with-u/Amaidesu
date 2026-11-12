"""OpenAITTSClient 特有行为：请求组装、音色形态、错误响应提取、音频流形态分流

六个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List

import pytest

from src.modules.tts.backends.openai.client import OpenAITTSApiError, OpenAITTSClient
from tests.modules.tts.helpers import sse_audio_delta, sse_event


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


def test_payload_requests_sse_stream_by_default() -> None:
    """默认点名 sse：audio 形态经网关常被整体缓冲，首字节要等整句合成完。"""
    assert make_client()._build_payload("你好")["stream_format"] == "sse"


def test_payload_omits_stream_format_when_audio_selected() -> None:
    """audio 是接口缺省形态，显式配置该形态时不携带该字段。"""
    assert "stream_format" not in make_client(stream_format="audio")._build_payload("你好")


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
    def __init__(
        self,
        status: int,
        body: List[bytes] | None = None,
        error_text: str = "",
        content_type: str = "audio/pcm",
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = _FakeContent(*(body or [b""])) if status == 200 else _FakeContent()
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
        self.posted: Dict[str, Any] = {}

    def post(self, url: str, **kwargs: Any) -> _FakeContext:
        self.posted = {"url": url, **kwargs}
        return _FakeContext(self._response)


async def _collect(client: OpenAITTSClient, text: str = "你好") -> list[bytes]:
    return [chunk async for chunk in client.stream_synthesize(text)]


def _delta_event(pcm: bytes) -> bytes:
    return sse_audio_delta(pcm)


def _sse_event(payload: Dict[str, Any]) -> bytes:
    return sse_event(payload)


async def test_stream_synthesize_yields_response_chunks_verbatim() -> None:
    """连续字节流形态：收到多少交多少，不重组、不解析。"""
    session = _FakeSession(_FakeResponse(200, [b"\x01\x02\x03\x04"]))
    client = make_client(stream_format="audio")
    client._session = session  # type: ignore[assignment]

    assert await _collect(client) == [b"\x01\x02\x03\x04"]
    assert session.posted["url"] == "https://api.openai.com/v1/audio/speech"
    assert session.posted["json"]["input"] == "你好"


async def test_stream_synthesize_decodes_sse_delta_payloads() -> None:
    """事件流形态：每块音频藏在 delta 的 base64 里，解出来按序交付。"""
    body = [_delta_event(b"\x01\x00" * 3), _delta_event(b"\x02\x00" * 2), _sse_event({"type": "speech.audio.done"})]
    client = make_client()
    client._session = _FakeSession(_FakeResponse(200, body, content_type="text/event-stream; charset=utf-8"))  # type: ignore[assignment]

    assert await _collect(client) == [b"\x01\x00" * 3, b"\x02\x00" * 2]


async def test_stream_synthesize_reassembles_sse_events_split_across_transport_chunks() -> None:
    """传输块可能把一条事件切在任意字节处：拼接后的事件必须完整解出。

    这是真实网络下的常态——事件边界与 TCP 分块无关，按定位符切会切坏 base64。
    """
    stream = _delta_event(b"\x03\x00" * 4) + _sse_event({"type": "speech.audio.done"})
    pieces = [stream[:9], stream[9:23], stream[23:]]
    client = make_client()
    client._session = _FakeSession(_FakeResponse(200, pieces, content_type="text/event-stream"))  # type: ignore[assignment]

    assert await _collect(client) == [b"\x03\x00" * 4]


async def test_stream_synthesize_flushes_trailing_event_without_blank_line() -> None:
    """服务端没给尾随空行时，最后一条事件仍要交付——否则丢掉每句话的结尾音频。"""
    body = [_delta_event(b"\x04\x00"), _delta_event(b"\x05\x00")[:-2]]  # 最后一条去掉空行
    client = make_client()
    client._session = _FakeSession(_FakeResponse(200, body, content_type="text/event-stream"))  # type: ignore[assignment]

    assert await _collect(client) == [b"\x04\x00", b"\x05\x00"]


async def test_stream_synthesize_raises_on_sse_error_event() -> None:
    """流内错误事件要变成业务异常，不能当作正常收尾静默结束。"""
    body = [_sse_event({"type": "speech.audio.error", "message": "engine overloaded"})]
    client = make_client()
    client._session = _FakeSession(_FakeResponse(200, body, content_type="text/event-stream"))  # type: ignore[assignment]

    with pytest.raises(OpenAITTSApiError, match="engine overloaded"):
        await _collect(client)


async def test_stream_synthesize_skips_unknown_and_empty_events() -> None:
    """未知事件类型与无载荷心跳不影响已收音频。"""
    body = [
        b": keep-alive\n\n",
        _sse_event({"type": "speech.audio.metadata", "foo": 1}),
        _delta_event(b"\x06\x00"),
        _sse_event({"type": "speech.audio.done"}),
    ]
    client = make_client()
    client._session = _FakeSession(_FakeResponse(200, body, content_type="text/event-stream"))  # type: ignore[assignment]

    assert await _collect(client) == [b"\x06\x00"]


async def test_stream_synthesize_raises_with_server_message_on_http_error() -> None:
    """HTTP 非 200 抛简短业务异常，携带服务端错误描述（便于直接看懂原因）。"""
    body = json.dumps({"error": {"message": "Incorrect API key provided."}})
    client = make_client()
    client._session = _FakeSession(_FakeResponse(401, error_text=body))  # type: ignore[assignment]

    with pytest.raises(OpenAITTSApiError, match="HTTP 401.*Incorrect API key"):
        await _collect(client)
