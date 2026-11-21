"""IndexTTSClient 真 HTTP 行为：连本地 aiohttp 服务跑通真实请求 / 头剥离 / 流式读取 / 错误路径

与 test_engine_contract.py 的分工：那个文件用替身客户端验证 Provider 编排
契约；本文件不替身 aiohttp，用 ``aiohttp.test_utils.TestServer`` 起真实
服务端，验证只有真发请求才会暴露的行为——WAV 头在任意分块边界上的剥离、
chunked 响应是否真的逐块到达、请求体形状、连接失败是否真的被包成业务异常。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, List

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.modules.tts.backends.index_tts.client import (
    IndexTTSClient,
    IndexTTSServiceError,
    strip_wav_header_and_align,
)

# 流式 WAV 头固定 44 字节（与客户端 _WAV_HEADER_BYTES 对应）
_WAV_HEADER = b"RIFF" + b"\x00" * 40


def make_client(server: TestServer, **overrides: Any) -> IndexTTSClient:
    url = server.make_url("/tts_stream")
    params: dict[str, Any] = {"host": url.host, "port": url.port}
    params.update(overrides)
    return IndexTTSClient(**params)


async def _collect(client: IndexTTSClient, text: str = "你好") -> List[bytes]:
    return [chunk async for chunk in client.stream_synthesize(text)]


def _stream_app(received: List[dict[str, Any]], *, status: int = 200) -> web.Application:
    """假 /tts_stream 端点：记录请求体，按"头部拆半 + 半个样本处切割"写流。"""

    async def handle(request: web.Request) -> web.StreamResponse:
        received.append(await request.json())
        if status != 200:
            return web.json_response({"detail": "text 不能为空"}, status=status)

        pcm = b"\x01\x00" * 1200  # 1200 样本
        response = web.StreamResponse(status=200, headers={"Content-Type": "audio/wav"})
        await response.prepare(request)
        # 狠切块：44 字节头部拆成 20 + 24 写，PCM 再切在半个样本中间——
        # 头剥离与样本对齐必须在任意传输分块边界上都成立
        await response.write(_WAV_HEADER[:20])
        await response.write(_WAV_HEADER[20:] + pcm[:2401])
        await response.write(pcm[2401:])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/tts_stream", handle)
    return app


async def test_stream_synthesize_strips_wav_header_across_chunk_boundaries() -> None:
    """真往返：请求体形状正确；44 字节头被完整剥掉，交付的是纯 PCM 字节序列。

    不断言块边界：``iter_any()`` 交付的是传输层分块，既可能被合并也可能被
    再切分，块边界不是接口契约，只有字节总序列是。
    """
    received: List[dict[str, Any]] = []
    server = TestServer(_stream_app(received))
    await server.start_server()
    client = make_client(server, seg_tokens=25)
    try:
        chunks = await _collect(client, "第一句")

        assert received[0]["text"] == "第一句"
        assert received[0]["seg_tokens"] == 25
        assert "emo_vector" not in received[0]  # 未配置情感向量时不携带
        joined = b"".join(chunks)
        assert not joined.startswith(b"RIFF")  # 头已剥除
        assert joined == b"\x01\x00" * 1200  # 字节总量精确（含半样本切割的重组）
        assert all(len(c) % 2 == 0 for c in chunks)  # 每个片段都是整样本，可直接 frombuffer(int16)
    finally:
        await client.close()
        await server.close()


async def _byte_stream(pieces: List[bytes]) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


def test_build_payload_emo_vector_per_call_overrides_bound() -> None:
    """情感向量：每请求覆盖优先于构造时绑定的静态配置；未配置时不携带。"""
    bound = IndexTTSClient(host="127.0.0.1", port=9888, emo_vector=[0.5, 0, 0, 0, 0, 0, 0, 0.5])
    assert bound._build_payload("x")["emo_vector"] == [0.5, 0, 0, 0, 0, 0, 0, 0.5]  # 回退静态
    assert bound._build_payload("x", emo_vector=[1.0, 0, 0, 0, 0, 0, 0, 0])["emo_vector"] == [
        1.0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]  # 每请求覆盖
    plain = IndexTTSClient(host="127.0.0.1", port=9888)
    assert "emo_vector" not in plain._build_payload("x")


async def test_header_strip_and_odd_sample_alignment() -> None:
    """头部拆块 + 半样本跨界（真机首测实锤的失败形态）：产出片段全为偶数
    字节，拼接后与 PCM 完全一致——奇数尾字节跨块暂存，不丢不重。"""
    pcm = bytes(range(256)) * 3  # 768 字节任意内容
    pieces = [
        _WAV_HEADER[:7],
        _WAV_HEADER[7:] + pcm[:1],  # 头尾粘 1 个字节
        pcm[1:100],
        pcm[100:201],  # 奇数长度块
        pcm[201:202],  # 单字节块
        pcm[202:],
    ]
    out = [chunk async for chunk in strip_wav_header_and_align(_byte_stream(pieces))]
    assert b"".join(out) == pcm
    assert all(len(chunk) % 2 == 0 for chunk in out)


async def test_header_entirely_in_separate_small_chunks() -> None:
    """头部被网络拆到极碎（每次 1 字节）：剥头计数跨块累计，PCM 零丢失。"""
    pcm = b"\x01\x00" * 100
    pieces = [bytes([b]) for b in _WAV_HEADER] + [pcm]
    out = [chunk async for chunk in strip_wav_header_and_align(_byte_stream(pieces))]
    assert b"".join(out) == pcm


async def test_payload_carries_emo_vector_only_when_configured() -> None:
    """情感向量：构造时给了非全零向量就携带，没给就不携带。"""
    received: List[dict[str, Any]] = []
    server = TestServer(_stream_app(received))
    await server.start_server()
    emo_client = make_client(server, emo_vector=[0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2])
    try:
        await _collect(emo_client, "开心一句")
        assert received[0]["emo_vector"] == [0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2]
    finally:
        await emo_client.close()
        await server.close()


async def test_streaming_arrives_before_generation_finishes() -> None:
    """边合成边下发：服务端写完首段后卡住，客户端必须已经拿到首段。

    这是"首段到达即出声"的底层前提——若客户端实现改成先攒满响应体再交付，
    本用例会因超时/顺序不符而失败。
    """
    release_key: web.AppKey[asyncio.Event] = web.AppKey("release_second_chunk")

    async def handle(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(_WAV_HEADER + b"\x01\x00" * 8)
        await request.app[release_key].wait()
        await response.write(b"\x02\x00" * 8)
        await response.write_eof()
        return response

    app = web.Application()
    release = asyncio.Event()
    app[release_key] = release
    app.router.add_post("/tts_stream", handle)
    server = TestServer(app)
    await server.start_server()
    client = make_client(server)
    try:
        stream = client.stream_synthesize("你好")
        first = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
        assert b"\x02" not in first  # 第二段尚未写入，收到的只能是首段
        release.set()
        rest = [chunk async for chunk in stream]
        assert b"".join([first, *rest]) == b"\x01\x00" * 8 + b"\x02\x00" * 8
    finally:
        await client.close()
        await server.close()


async def test_stream_synthesize_surfaces_server_error_over_real_http() -> None:
    """真实非 200：错误体被收进一句可读原因，且不产出任何音频块。"""
    server = TestServer(_stream_app([], status=500))
    await server.start_server()
    client = make_client(server)
    try:
        with pytest.raises(IndexTTSServiceError, match="HTTP 500"):
            await _collect(client)
    finally:
        await client.close()
        await server.close()


async def test_stream_synthesize_wraps_connection_failure_as_business_error() -> None:
    """服务不可达：aiohttp 连接异常被包成 IndexTTSServiceError（只留简短原因），
    且原始异常经 __cause__ 保留供调试——这是引擎"环境故障不打全量 traceback"
    契约的前提。"""
    client = IndexTTSClient(host="127.0.0.1", port=1)  # 1 号端口不会有服务
    try:
        with pytest.raises(IndexTTSServiceError, match="连接 IndexTTS 服务失败") as excinfo:
            await _collect(client)
        assert isinstance(excinfo.value.__cause__, Exception)
    finally:
        await client.close()


async def test_close_is_idempotent() -> None:
    server = TestServer(_stream_app([]))
    await server.start_server()
    client = make_client(server)
    try:
        await _collect(client)
        await client.close()
        await client.close()  # 二次关闭不得抛
    finally:
        await server.close()


async def test_provider_synthesizes_end_to_end_against_real_server(event_bus) -> None:
    """整条链路真跑：真 HTTP 服务 → 真客户端 → 真 Provider → 替身声卡。

    只替身两端：IndexTTS 的服务器（用本地假端点代替）与声卡（没有测试硬件）。
    中间的请求组装、头剥离、流式读取、样本拼接、事件派发全是生产代码。
    """
    from src.modules.events.names import CoreEvents
    from src.modules.tts.backends.index_tts.provider import IndexTTSProvider
    from tests.modules.tts.helpers import make_audio_manager, record_lifecycle, setup_with_audio_manager

    server = TestServer(_stream_app([]))
    await server.start_server()
    try:
        url = server.make_url("/tts_stream")
        provider = IndexTTSProvider(
            config={"type": "index_tts", "host": url.host, "port": url.port, "sample_rate": 22050},
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
        assert finished.duration_ms == int(1200 * 1000 / 22050)  # 54ms
    finally:
        await server.close()
