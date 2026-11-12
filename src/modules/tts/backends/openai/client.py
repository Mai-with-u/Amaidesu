"""OpenAI 语音合成客户端（``POST /v1/audio/speech``）

对接 OpenAI 官方语音合成端点：请求体描述"念什么、用什么模型与音色、以
什么封装返回"，响应体是音频字节流（HTTP chunked transfer encoding，边合成
边下发）。本客户端把这条线翻译成"PCM 字节片段异步流"，供 Provider 逐块写
声卡。

- 认证：``Authorization: Bearer <api_key>`` 请求头
- 封装固定 ``pcm``：服务端返回裸 PCM（24kHz、16-bit signed、little-endian、
  无封装头），解码与逐块写出都无需解析容器；mp3/opus 等封装形态在流式
  逐块播放时另有帧边界问题。采样率由接口固定，不可配
- 流式形态显式发送 ``stream_format=sse``：``sse`` 是事件流（每块 base64
  音频装在 JSON 事件里），``audio`` 是连续音频字节流。两者在服务端都是边
  合成边下发，但经反向代理/网关转发时 ``audio`` 的二进制 body 常被整体
  缓冲（首字节要等整句合成完，实测 15 秒级），``sse`` 则被逐行透传——
  所以默认走 ``sse``，配置可退回 ``audio``
- 响应形态按 ``Content-Type`` 判而不是按请求参数判：服务端可能忽略
  ``stream_format`` 仍回连续音频（如老版本 tts-1 系列、未适配的兼容端点），
  此时直接透传字节流，不必为兼容性多开一条配置分支
- 风格提示 ``instructions`` 仅 gpt-4o-mini-tts 生效；调用方决定是否携带，
  本客户端只负责"非空才放进请求体"
- 音色字段只接内置音色名（字符串）：自定义克隆音色需要传对象形态
  ``{"id": ...}``，其 ID 命名与申请流程随账号开通方式而变，不在此猜测
"""

from __future__ import annotations

import base64
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from src.modules.logging import get_logger

# 连接超时 / 单次读超时（秒）：读超时须覆盖服务端首块生成与块间生成的最坏间隔
_CONNECT_TIMEOUT_S = 10.0
_SOCK_READ_TIMEOUT_S = 120.0

# 响应封装：裸 PCM 逐块即播，无需容器解析（采样率 24kHz 由接口固定）
_RESPONSE_FORMAT = "pcm"

# 流式形态：sse 事件流经网关逐行透传，audio 连续字节流常被网关整体缓冲
_STREAM_FORMAT_SSE = "sse"

# SSE 结构：事件之间空行分隔，载荷在 data: 行（内容为单行 JSON）
_SSE_EVENT_DELIMITERS = (b"\n\n", b"\r\n\r\n")
_SSE_DATA_FIELD = "data:"
_SSE_CONTENT_TYPE = "text/event-stream"

# SSE 事件类型：增量音频 / 正常收尾 / 流内错误
_SSE_EVENT_DELTA = "speech.audio.delta"
_SSE_EVENT_DONE = "speech.audio.done"
_SSE_EVENT_ERROR = "speech.audio.error"


class OpenAITTSApiError(Exception):
    """OpenAI 语音合成接口不可达 / 请求被拒。

    aiohttp 的连接类异常字符串携带完整 URL 与底层连接池细节，直接抛出会
    把日志刷成数百行 traceback；本异常只保留对排查有用的简短信息（状态码
    + 服务端错误描述），原始异常经 ``__cause__`` 保留供调试器查看。
    """


class OpenAITTSClient:
    """OpenAI 语音合成客户端：文本进，PCM 字节片段流出。

    合成参数（模型 / 音色 / 风格提示 / 语速）在构造时一次性绑定，逐句合成
    只传文本——与端点"每请求自足描述"的形态对应，绑定来源是引擎配置而非
    会话状态（服务端零会话）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        instructions: str = "",
        speed: float = 1.0,
        stream_format: str = _STREAM_FORMAT_SSE,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.voice = voice.strip()
        self.instructions = instructions.strip()
        self.speed = speed
        self.stream_format = stream_format.strip().lower()
        self.logger = get_logger("OpenAITTSClient")
        self._session: Optional[aiohttp.ClientSession] = None

    # ----- 请求组装（纯函数，测试直接断言形状）-----

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_payload(self, text: str) -> Dict[str, Any]:
        """组装请求体：必填三项（模型/文本/音色）+ 可选风格提示与语速。"""
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": _RESPONSE_FORMAT,
            "speed": self.speed,
        }
        # 空提示不携带：该字段对 tts-1 系列无效，发了只会被静默忽略
        if self.instructions:
            payload["instructions"] = self.instructions
        # audio 是接口缺省形态，不必显式声明；只有 sse 需要点名
        if self.stream_format == _STREAM_FORMAT_SSE:
            payload["stream_format"] = _STREAM_FORMAT_SSE
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

    async def stream_synthesize(self, text: str) -> AsyncIterator[bytes]:
        """合成一段文本，逐块产出 PCM 字节片段。

        收到多少音频就往下游交多少——不按帧对齐、不做缓冲重组，块边界由
        服务端下发节奏决定。响应形态按 ``Content-Type`` 分流：事件流解析出
        每块音频，连续字节流直接透传。HTTP 非 200 抛
        :class:`OpenAITTSApiError`，服务端错误描述截断保留。

        取消语义：调用方取消后本异步生成器随 GC 关闭，aiohttp 的请求上下文
        退出即释放连接（被取消句数量级低，不做显式提前关闭）。
        """
        session = await self._ensure_session()
        try:
            async with session.post(
                self.base_url, headers=self._build_headers(), json=self._build_payload(text)
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise OpenAITTSApiError(f"接口返回 HTTP {response.status}: {self._error_detail(body)}")
                if _SSE_CONTENT_TYPE in response.headers.get("Content-Type", ""):
                    async for pcm in self._iter_sse_audio(response.content):
                        yield pcm
                else:
                    async for chunk in response.content.iter_any():
                        if chunk:
                            yield chunk
        except aiohttp.ClientError as e:
            raise OpenAITTSApiError(f"连接 OpenAI 失败（{type(e).__name__}）: {e}") from e

    async def _iter_sse_audio(self, body: Any) -> AsyncIterator[bytes]:
        """从事件流里逐块取出音频载荷。

        事件之间的空行是唯一可靠边界，按它切块；传输块可能把事件切在任意
        字节处，所以未凑满一条事件的残余留在缓冲里等下一个传输块。流结束时
        缓冲里若还压着一条事件（服务端没补尾随空行），补发一次解析——不
        flush 会丢掉每句话的最后一段音频。
        """
        buffer = b""
        async for chunk in body.iter_any():
            buffer += chunk
            while True:
                boundary = self._find_event_boundary(buffer)
                if boundary is None:
                    break
                event, buffer = buffer[: boundary[0]], buffer[boundary[1] :]
                pcm = self._parse_event(event)
                if pcm is None:
                    return  # 收尾事件：此后不应再有音频
                if pcm:
                    yield pcm
        pcm = self._parse_event(buffer)
        if pcm:
            yield pcm

    @staticmethod
    def _find_event_boundary(buffer: bytes) -> Optional[tuple[int, int]]:
        """定位最早的事件结束符，返回 (事件体末端, 下一事件起点)；未出现返回 None。"""
        best: Optional[tuple[int, int]] = None
        for delimiter in _SSE_EVENT_DELIMITERS:
            index = buffer.find(delimiter)
            if index == -1:
                continue
            start = index + len(delimiter)
            if best is None or index < best[0]:
                best = (index, start)
        return best

    @classmethod
    def _parse_event(cls, event: bytes) -> Optional[bytes]:
        """解析单条事件：返回音频字节；空音频事件返回 ``b""``；收尾事件返回 ``None``。

        事件体内可能有 ``event:`` / ``id:`` 等字段行，音频只认 ``data:`` 行。
        """
        payload_text = cls._event_data(event)
        if not payload_text:
            return b""  # 心跳注释行等无载荷事件
        try:
            data = json.loads(payload_text)
        except json.JSONDecodeError as e:
            raise OpenAITTSApiError(f"事件流载荷不是合法 JSON: {payload_text[:100]}") from e
        event_type = data.get("type")
        if event_type == _SSE_EVENT_DONE:
            return None
        if event_type == _SSE_EVENT_ERROR:
            raise OpenAITTSApiError(f"服务端中断合成: {data.get('message', '未提供原因')}")
        if event_type != _SSE_EVENT_DELTA:
            return b""  # 未知事件类型：向后兼容新事件，跳过不影响已收音频
        delta = data.get("delta")
        if not delta:
            return b""
        try:
            return base64.b64decode(delta)
        except (ValueError, TypeError) as e:
            raise OpenAITTSApiError("事件流音频块不是合法 base64") from e

    @staticmethod
    def _event_data(event: bytes) -> str:
        """取事件体里 ``data:`` 行的载荷；多条 data 行按 SSE 规范以换行拼接。"""
        lines: List[str] = []
        for raw_line in event.replace(b"\r\n", b"\n").split(b"\n"):
            if not raw_line.startswith(_SSE_DATA_FIELD.encode()):
                continue
            value = raw_line[len(_SSE_DATA_FIELD) :].lstrip()
            lines.append(value.decode("utf-8", errors="replace"))
        return "\n".join(lines)

    @staticmethod
    def _error_detail(body: str) -> str:
        """从错误响应体提取可读描述；非 JSON 或形状不符时原样截断。"""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return body[:200]
        error = data.get("error") if isinstance(data, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return str(message)[:200] if message else body[:200]


__all__ = ["OpenAITTSApiError", "OpenAITTSClient"]
