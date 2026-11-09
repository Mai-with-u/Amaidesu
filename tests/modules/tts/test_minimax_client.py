"""MiniMaxTTSClient 特有行为：请求组装、SSE 块解析、双层状态错误"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from src.modules.tts.backends.minimax.client import MiniMaxTTSApiError, MiniMaxTTSClient


def make_client(**overrides: Any) -> MiniMaxTTSClient:
    params: dict[str, Any] = {
        "base_url": "https://api.minimax.io/v1/t2a_v2",
        "api_key": "mk-test",
        "group_id": "999888777",
        "model": "speech-02-hd",
        "voice_id": "female-tianmei",
        "sample_rate": 24000,
    }
    params.update(overrides)
    return MiniMaxTTSClient(**params)


class _FakeBody:
    """aiohttp 响应体替身：iter_any 按给定字节序列吐块。"""

    def __init__(self, *pieces: bytes) -> None:
        self._pieces = list(pieces)

    async def iter_any(self) -> AsyncIterator[bytes]:
        for piece in self._pieces:
            yield piece


async def _collect(client: MiniMaxTTSClient, body: _FakeBody) -> list[bytes]:
    return [pcm async for pcm in client._iter_sse_audio(body)]


# ----- 请求组装 -----


def test_url_appends_group_id_only_when_set() -> None:
    assert make_client()._build_url() == "https://api.minimax.io/v1/t2a_v2?GroupId=999888777"
    assert make_client(group_id="")._build_url() == "https://api.minimax.io/v1/t2a_v2"


def test_payload_carries_streaming_shape_and_pcm_audio() -> None:
    payload = make_client(emotion="happy")._build_payload("你好")
    assert payload["model"] == "speech-02-hd" and payload["text"] == "你好"
    assert payload["stream"] is True
    assert payload["stream_options"] == {"exclude_aggregated_audio": True}  # 逐块播放下聚合块是重复内容
    assert payload["voice_setting"] == {
        "voice_id": "female-tianmei",
        "speed": 1.0,
        "vol": 1.0,
        "pitch": 0,
        "emotion": "happy",
    }
    assert payload["audio_setting"] == {"sample_rate": 24000, "format": "pcm", "channel": 1}


def test_payload_omits_empty_emotion() -> None:
    assert "emotion" not in make_client(emotion="")._build_payload("你好")["voice_setting"]


# ----- SSE 块解析 -----


def _sse_block(payload: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


async def test_iter_sse_audio_decodes_hex_and_skips_non_audio_blocks() -> None:
    client = make_client()
    audio_hex = b"0102ffff"
    body = _FakeBody(
        _sse_block({"data": {"audio": audio_hex.decode(), "status": 1}}),
        b'data: {"extra_info": {"audio_length": 100}}\n\n',  # 收尾统计块：无音频
        _sse_block({"data": {"audio": "00ff", "status": 1}}),
    )
    assert await _collect(client, body) == [bytes.fromhex("0102ffff"), bytes.fromhex("00ff")]


async def test_iter_sse_audio_reassembles_blocks_split_across_transfers() -> None:
    """定位符与 JSON 体被传输块边界截断时仍能正确切块。"""
    client = make_client()
    whole = _sse_block({"data": {"audio": "deadbeef", "status": 1}}) + _sse_block(
        {"data": {"audio": "aa", "status": 1}}
    )
    # 从中间劈开两半，模拟传输层任意切分
    cut = len(whole) // 2
    assert await _collect(client, _FakeBody(whole[:cut], whole[cut:])) == [
        bytes.fromhex("deadbeef"),
        bytes.fromhex("aa"),
    ]


async def test_parse_block_raises_on_business_error_despite_http_success() -> None:
    """双层状态：HTTP 200 的业务错误块必须抛错而非当音频处理。"""
    client = make_client()
    block = _sse_block({"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}})
    with pytest.raises(MiniMaxTTSApiError, match="1004"):
        client._parse_block(block)


async def test_parse_block_raises_on_invalid_hex() -> None:
    client = make_client()
    block = _sse_block({"data": {"audio": "not-hex!", "status": 1}})
    with pytest.raises(MiniMaxTTSApiError, match="hex"):
        client._parse_block(block)


async def test_parse_block_raises_on_invalid_json() -> None:
    client = make_client()
    with pytest.raises(MiniMaxTTSApiError, match="JSON"):
        client._parse_block(b"data: {broken")


async def test_stream_synthesize_raises_wrapped_error_on_http_failure() -> None:
    """HTTP 非 200 抛简短业务异常（原始响应体截断保留，连接细节不上日志面）。"""
    client = make_client()

    class _FakeSession:
        closed = False

        def post(self, *args: Any, **kwargs: Any) -> Any:
            return _FakeFailedContext()

    class _FakeFailedContext:
        async def __aenter__(self) -> Any:
            return _FakeFailedResponse()

        async def __aexit__(self, *args: Any) -> None:
            return None

    class _FakeFailedResponse:
        status = 401

        async def text(self) -> str:
            return '{"base_resp":{"status_code":1004}}'

    client._session = _FakeSession()  # type: ignore[assignment]
    with pytest.raises(MiniMaxTTSApiError, match="HTTP 401"):
        async for _ in client.stream_synthesize("你好"):
            pass
