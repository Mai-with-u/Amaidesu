"""IndexTTS 本地 HTTP 合成客户端（对接自建本地常驻服务）

IndexTTS 官方只提供 Python API 与给人用的 WebUI，没有 HTTP 服务；本引擎
对接的是仓库外自建的本地常驻 HTTP 服务（FastAPI 薄包装，"本地进程 HTTP"
先例同 GPT-SoVITS api_v2）。流式端点 ``POST /tts_stream`` 的请求体为
``{"text", "seg_tokens", "emo_vector"?}``，响应是一条 chunked 连续字节流：
44 字节 WAV 流式头（总长度占位）+ 裸 PCM int16 单声道依次拼接，服务端边
合成边推（按文本切段，每段合成完即推送）。

网络分块边界与合成段边界不对齐，本客户端按头部固定长度剥掉 WAV 头后把
余下字节按整样本（2 字节）对齐产出。头部剥离用字节计数而非 RIFF 探测：
裸 PCM 内容里可能偶然出现 "data" 四字节序列，探测法会误判。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

import aiohttp

from src.modules.logging import get_logger

# 连接超时 / 单次读超时（秒）：读超时须覆盖服务端单段合成的最坏耗时——
# 显存与桌面负载会把单段拖到分钟级（实测劣化 18 倍），300s 容忍中度劣化，
# 彻底溢出仍会超时报错而非无限等
_CONNECT_TIMEOUT_S = 10.0
_SOCK_READ_TIMEOUT_S = 300.0

# 流式 WAV 头固定 44 字节（RIFF/fmt/data 三段，总长度字段为占位值）
_WAV_HEADER_BYTES = 44


class IndexTTSServiceError(Exception):
    """IndexTTS 本地服务不可达 / 请求失败。

    aiohttp 的连接类异常字符串携带完整 URL 与底层连接池细节，直接抛出
    会把日志刷成数百行 traceback；本异常只保留对排查有用的简短信息
    （服务地址 + 失败类别），原始异常经 ``__cause__`` 保留供调试器查看。
    """


async def strip_wav_header_and_align(
    byte_chunks: AsyncIterator[bytes], header_bytes: int = _WAV_HEADER_BYTES
) -> AsyncIterator[bytes]:
    """剥掉流式头部的固定字节数，并把余下字节按整样本（2 字节）对齐产出。

    网络分块边界与样本边界不对齐：头部可能被拆到多个读块、半个样本可能
    落在块边界上（真机首测实锤 ``buffer size must be a multiple of element
    size``）。头部按字节计数剥除；对齐靠跨块暂存奇数尾字节——产出的每个
    片段都是偶数字节，可直接 ``np.frombuffer(int16)``。流结束时仍挂着的
    不足一个样本的尾字节丢弃（无声段）。
    """
    header_remaining = header_bytes
    pending_odd_byte = b""
    async for chunk in byte_chunks:
        if header_remaining > 0:
            if len(chunk) <= header_remaining:
                header_remaining -= len(chunk)
                continue
            chunk = chunk[header_remaining:]
            header_remaining = 0
        if pending_odd_byte:
            chunk = pending_odd_byte + chunk
            pending_odd_byte = b""
        if len(chunk) % 2:
            pending_odd_byte = chunk[-1:]
            chunk = chunk[:-1]
        if chunk:
            yield chunk


class IndexTTSClient:
    """IndexTTS 本地服务流式合成客户端：文本进，裸 PCM 字节片段流出。

    请求参数（分段上限 / 情感向量）在构造时一次性绑定，逐句合成只传
    文本；说话人参考音频由服务端启动参数持有，客户端不感知。
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        seg_tokens: int = 25,
        emo_vector: Optional[list[float]] = None,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.seg_tokens = seg_tokens
        self.emo_vector = list(emo_vector) if emo_vector else None
        self.logger = get_logger("IndexTTSClient")
        self._session: Optional[aiohttp.ClientSession] = None

    # ----- 请求组装（纯函数，测试直接断言形状）-----

    def _build_payload(self, text: str, emo_vector: Optional[list[float]] = None) -> Dict[str, Any]:
        """组装请求体：文本 + 分段上限；情感向量仅在使用时携带。

        ``emo_vector`` 是本请求的动态覆盖（情绪映射产出）；None 时回退构造
        时绑定的静态配置向量。
        """
        payload: Dict[str, Any] = {"text": text, "seg_tokens": self.seg_tokens}
        effective = emo_vector if emo_vector is not None else self.emo_vector
        if effective is not None:
            payload["emo_vector"] = list(effective)
        return payload

    # ----- 会话与合成 -----

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """取复用会话（惰性创建；句间复用连接池）。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=None, connect=_CONNECT_TIMEOUT_S, sock_read=_SOCK_READ_TIMEOUT_S)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """关闭底层会话（幂等）。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def synthesize_full(self, text: str, emo_vector: Optional[list[float]] = None) -> bytes:
        """整段合成：等服务端合成完毕，一次性返回完整 WAV 字节。

        走 ``POST /tts`` 整段端点（服务端内部仍是低显存分段合成后拼接），
        供调试试听等不逐块写声卡的场景。``emo_vector`` 语义同
        :meth:`stream_synthesize`。HTTP 非 200 抛 :class:`IndexTTSServiceError`。
        """
        session = await self._ensure_session()
        try:
            async with session.post(f"{self.base_url}/tts", json=self._build_payload(text, emo_vector)) as response:
                if response.status != 200:
                    body = await response.text()
                    raise IndexTTSServiceError(f"接口返回 HTTP {response.status}: {body[:200]}")
                return await response.read()
        except aiohttp.ClientError as e:
            raise IndexTTSServiceError(f"连接 IndexTTS 服务失败（{type(e).__name__}）: {e}") from e

    async def stream_synthesize(self, text: str, emo_vector: Optional[list[float]] = None) -> AsyncIterator[bytes]:
        """合成一段文本，逐块产出裸 PCM 字节片段（int16 单声道）。

        ``emo_vector`` 是本请求的动态情感向量（情绪映射产出）；None 时回退
        构造时绑定的静态配置向量。服务端边合成边推；头部剥除按固定 44 字节
        计数（网络分块可能把头部
        与首段 PCM 合并在同一读块里，也可能把头部拆到多个读块里，计数法对
        两种都对）。HTTP 非 200 抛 :class:`IndexTTSServiceError`。

        取消语义：调用方取消后本异步生成器随 GC 关闭，aiohttp 的请求
        上下文退出即释放连接（被取消句数量级低，不做显式提前关闭）。
        """
        session = await self._ensure_session()
        try:
            async with session.post(
                f"{self.base_url}/tts_stream", json=self._build_payload(text, emo_vector)
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise IndexTTSServiceError(f"接口返回 HTTP {response.status}: {body[:200]}")
                async for pcm in strip_wav_header_and_align(response.content.iter_any()):
                    yield pcm
        except aiohttp.ClientError as e:
            raise IndexTTSServiceError(f"连接 IndexTTS 服务失败（{type(e).__name__}）: {e}") from e


__all__ = ["IndexTTSClient", "IndexTTSServiceError", "strip_wav_header_and_align"]
