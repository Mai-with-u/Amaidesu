"""OpenAITTSClient 真 HTTP 行为：连本地 aiohttp 服务跑通真实请求 / 流式读取 / 错误路径

与 test_openai_client.py 的分工：那个文件用替身 session 断言请求组装与
错误文本提取（纯函数形状）；本文件不替身 aiohttp，用 ``aiohttp.test_utils.TestServer``
起真实服务端，验证只有真发请求才会暴露的行为——Bearer 头是否真的送出、
chunked 响应是否真的逐块到达、连接失败是否真的被包成业务异常。
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import numpy as np

from src.modules.tts.backends.openai.client import OpenAITTSApiError, OpenAITTSClient
from tests.modules.tts.helpers import sse_audio_delta, sse_event

# 服务端识别的测试密钥：客户端未按规范携带 Bearer 头时，服务端据此拒签
_EXPECTED_AUTH = "Bearer sk-local"

# aiohttp 要求 app 配置键用 AppKey（字符串键会发 NotAppKeyWarning）
_RELEASE_SECOND_CHUNK: web.AppKey[asyncio.Event] = web.AppKey("release_second_chunk")


def make_client(base_url: str, **overrides: Any) -> OpenAITTSClient:
    params: dict[str, Any] = {
        "base_url": base_url,
        "api_key": "sk-local",
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
    }
    params.update(overrides)
    return OpenAITTSClient(**params)


async def _collect(client: OpenAITTSClient, text: str = "你好") -> List[bytes]:
    return [chunk async for chunk in client.stream_synthesize(text)]


def _speech_app(received: List[dict[str, Any]], *, status: int = 200) -> web.Application:
    """假 speech 端点：校验 Bearer 头、记录请求体，按块写出 PCM 字节流。"""

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.headers.get("Authorization") != _EXPECTED_AUTH:
            return web.json_response({"error": {"message": "Incorrect API key provided."}}, status=401)
        received.append(await request.json())
        if status != 200:
            return web.json_response({"error": {"message": "model overloaded"}}, status=status)

        response = web.StreamResponse(status=200, headers={"Content-Type": "application/octet-stream"})
        await response.prepare(request)
        # 分三块写出：验证客户端逐块交付（不攒满再发）
        for piece in (b"\x01\x00" * 4, b"\x02\x00" * 4, b"\x03\x00" * 4):
            await response.write(piece)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/audio/speech", handle)
    return app


async def test_stream_synthesize_round_trips_against_real_http_server() -> None:
    """真实请求-响应往返：Bearer 认证通过、请求体形状正确、PCM 字节完整到达。

    不断言块边界：``iter_any()`` 交付的是传输层分块，既可能被合并也可能被
    再切分，块边界不是接口契约，只有字节总序列是。
    """
    received: List[dict[str, Any]] = []
    server = TestServer(_speech_app(received))
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")))
    try:
        chunks = await _collect(client, "第一句")

        assert received[0]["input"] == "第一句"
        assert received[0]["response_format"] == "pcm"
        assert received[0]["voice"] == "alloy"
        assert len(chunks) >= 1
        assert b"".join(chunks) == b"\x01\x00" * 4 + b"\x02\x00" * 4 + b"\x03\x00" * 4
    finally:
        await client.close()
        await server.close()


async def test_stream_synthesize_surfaces_server_error_message_over_real_http() -> None:
    """真实 401：服务端 JSON 错误体经 _error_detail 提取成一句可读原因。"""
    server = TestServer(_speech_app([], status=200))
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")), api_key="sk-wrong")
    try:
        with pytest.raises(OpenAITTSApiError, match="HTTP 401.*Incorrect API key"):
            await _collect(client)
    finally:
        await client.close()
        await server.close()


async def test_stream_synthesize_wraps_connection_failure_as_business_error() -> None:
    """端点不可达：aiohttp 连接异常被包成 OpenAITTSApiError（只留简短原因），
    且原始异常经 __cause__ 保留供调试——这是引擎"环境故障不打全量 traceback"
    契约的前提。"""
    client = make_client("http://127.0.0.1:1/v1/audio/speech")  # 1 号端口不会有服务
    try:
        with pytest.raises(OpenAITTSApiError, match="连接 OpenAI 失败") as excinfo:
            await _collect(client)
        assert isinstance(excinfo.value.__cause__, Exception)
    finally:
        await client.close()


async def test_streaming_arrives_before_generation_finishes() -> None:
    """边生成边下发：服务端写第二块前卡住，客户端必须已经拿到第一块。

    这是"首块到达即出声"的底层前提——若客户端实现改成先攒满响应体再交付，
    本用例会因超时/顺序不符而失败。
    """

    async def handle(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b"\x01\x00" * 8)
        await request.app[_RELEASE_SECOND_CHUNK].wait()  # 等测试放行再写第二块
        await response.write(b"\x02\x00" * 8)
        await response.write_eof()
        return response

    app = web.Application()
    release = asyncio.Event()
    app[_RELEASE_SECOND_CHUNK] = release
    app.router.add_post("/v1/audio/speech", handle)
    server = TestServer(app)
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")))
    try:
        stream = client.stream_synthesize("你好")
        first = await stream.__anext__()
        # 第一块到手时第二块还没开始写：证明是流式交付，不是攒满再给
        assert b"\x02" not in first
        release.set()
        rest = [chunk async for chunk in stream]
        assert b"".join([first, *rest]) == b"\x01\x00" * 8 + b"\x02\x00" * 8
    finally:
        await client.close()
        await server.close()


async def test_client_reuses_one_session_across_sentences() -> None:
    """句间复用连接池：两句合成后底层 session 是同一个实例。"""
    received: List[dict[str, Any]] = []
    server = TestServer(_speech_app(received))
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")))
    try:
        await _collect(client, "第一句")
        first_session = client._session
        await _collect(client, "第二句")

        assert client._session is first_session
        assert len(received) == 2
    finally:
        await client.close()
        await server.close()


async def test_close_is_idempotent_and_drops_session() -> None:
    server = TestServer(_speech_app([]))
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")))
    try:
        await _collect(client)
        await client.close()
        await client.close()  # 二次关闭不得抛
        assert client._session is None
    finally:
        await server.close()


def _sse_app(release: asyncio.Event, received: List[dict[str, Any]]) -> web.Application:
    """假事件流端点：首块立即写，第二块等测试放行——用来验证"边合成边到达"。"""

    async def handle(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream; charset=utf-8"})
        await response.prepare(request)
        await response.write(sse_audio_delta(b"\x01\x00" * 8))
        await release.wait()
        await response.write(sse_audio_delta(b"\x02\x00" * 8))
        await response.write(sse_event({"type": "speech.audio.done"}))
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/audio/speech", handle)
    return app


async def test_sse_audio_arrives_before_generation_finishes() -> None:
    """事件流真透传：服务端写完首块后卡住，客户端必须已经交出首块音频。

    这是「首块到达即出声」的端到端证据。走 audio 形态时同样的服务端行为
    会被网关整体缓冲，首字节要等到合成结束——本用例守住 sse 的增量语义。
    """
    received: List[dict[str, Any]] = []
    release = asyncio.Event()
    server = TestServer(_sse_app(release, received))
    await server.start_server()
    client = make_client(str(server.make_url("/v1/audio/speech")))
    try:
        stream = client.stream_synthesize("你好")
        first = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
        assert first == b"\x01\x00" * 8  # 第二块尚未写入，收到的只能是第一块
        release.set()
        rest = [chunk async for chunk in stream]
        assert b"".join(rest) == b"\x02\x00" * 8
        assert received[0]["stream_format"] == "sse"
    finally:
        await client.close()
        await server.close()


async def test_provider_synthesizes_end_to_end_against_real_server(event_bus) -> None:
    """整条链路真跑：真 HTTP 服务 → 真客户端 → 真 Provider → 替身声卡。

    只替身两端：OpenAI 的服务器（用本地假端点代替）与声卡（没有测试硬件）。
    中间的请求组装、流式读取、样本拼接、事件派发全是生产代码。这条用例是
    "没有 API 密钥时能拿到的最强证据"——它覆盖了 mock 测试覆盖不到的分块
    边界与真实 aiohttp 行为。
    """
    from src.modules.events.names import CoreEvents
    from src.modules.tts.backends.openai.provider import OpenAITTSProvider
    from tests.modules.tts.helpers import make_audio_manager, record_lifecycle, setup_with_audio_manager

    # 服务端按奇数字节切块，专门压分块对齐逻辑
    pcm = b"\x01\x00" * 1200  # 1200 样本 = 50ms

    async def handle(request: web.Request) -> web.StreamResponse:
        await request.json()
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(pcm[:2401])  # 切在半个样本中间
        await response.write(pcm[2401:])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/audio/speech", handle)
    server = TestServer(app)
    await server.start_server()
    try:
        provider = OpenAITTSProvider(
            config={
                "type": "openai_tts",
                "base_url": str(server.make_url("/v1/audio/speech")),
                "api_key": "sk-local",
                "voice": "alloy",
            },
            event_bus=event_bus,
        )
        captured = record_lifecycle(event_bus)
        mgr = await setup_with_audio_manager(provider, make_audio_manager())
        try:
            await provider.handle_speech("你好", utterance_id="utt_e2e")
            await event_bus.cleanup()
        finally:
            await provider.cleanup()

        written = [call.args[0] for call in mgr.write_chunk.call_args_list]
        assert np.concatenate(written).size == 1200
        (finished,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FINISHED]
        assert finished.duration_ms == 50
    finally:
        await server.close()
