"""OpenAI 语音合成客户端（``POST /v1/audio/speech``）

对接 OpenAI 官方语音合成端点：请求体描述"念什么、用什么模型与音色、以
什么封装返回"，响应体是音频字节流（HTTP chunked transfer encoding，边合成
边下发）。本客户端把这条线翻译成"PCM 字节片段异步流"，供 Provider 逐块写
声卡。

- 认证：``Authorization: Bearer <api_key>`` 请求头
- 封装固定 ``pcm``：服务端返回裸 PCM（24kHz、16-bit signed、little-endian、
  无封装头），解码与逐块写出都无需解析容器；mp3/opus 等封装形态在流式
  逐块播放时另有帧边界问题。采样率由接口固定，不可配
- 流式开关不显式发送：``stream_format`` 缺省即 ``audio``（音频字节流），
  ``sse`` 事件流反而要多一层解析且 tts-1 系列不支持
- 风格提示 ``instructions`` 仅 gpt-4o-mini-tts 生效；调用方决定是否携带，
  本客户端只负责"非空才放进请求体"
- 音色字段只接内置音色名（字符串）：自定义克隆音色需要传对象形态
  ``{"id": ...}``，其 ID 命名与申请流程随账号开通方式而变，不在此猜测
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Optional

import aiohttp

from src.modules.logging import get_logger

# 连接超时 / 单次读超时（秒）：读超时须覆盖服务端首块生成与块间生成的最坏间隔
_CONNECT_TIMEOUT_S = 10.0
_SOCK_READ_TIMEOUT_S = 120.0

# 响应封装：裸 PCM 逐块即播，无需容器解析（采样率 24kHz 由接口固定）
_RESPONSE_FORMAT = "pcm"


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
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.voice = voice.strip()
        self.instructions = instructions.strip()
        self.speed = speed
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

        响应体是音频字节流（chunked），收到多少字节就往下游交多少——不按
        帧对齐、不做缓冲重组，块边界由传输层决定。HTTP 非 200 抛
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
                async for chunk in response.content.iter_any():
                    if chunk:
                        yield chunk
        except aiohttp.ClientError as e:
            raise OpenAITTSApiError(f"连接 OpenAI 失败（{type(e).__name__}）: {e}") from e

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
