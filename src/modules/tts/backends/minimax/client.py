"""MiniMax T2A 流式合成客户端（SSE 方言）

对接 MiniMax 开放平台同步合成接口 ``POST /v1/t2a_v2`` 的流式形态
（``stream: true``）：音频以 hex 编码分块内嵌于 SSE（Server-Sent Events，
HTTP 响应里 ``data: <JSON>`` 行式推送）的 JSON 块中，服务端边合成边推，
中间块各带一段增量音频，收尾块另带 ``extra_info`` 用量统计。本客户端把
这条方言翻译成"PCM 字节片段异步流"，供 Provider 逐块写声卡。

- 认证：``Authorization: Bearer <api_key>`` 请求头；``group_id`` 非空时
  拼接 ``?GroupId=<group_id>`` 查询参数（真机实测仅凭密钥即可调用，
  部分账号要求 GroupId 时填写）
- 双层状态：该接口 HTTP 层几乎总回 200，真实成败在响应 JSON 的
  ``base_resp.status_code``（0=成功）——只看 HTTP 状态码会把业务错误
  响应当音频处理；HTTP 非 200 与流内业务码非 0 都在此抛 :class:`MiniMaxTTSApiError`
- 聚合块排除：流式收尾默认会重发整段聚合音频，请求固定携带
  ``stream_options.exclude_aggregated_audio=true``——逐块播放语义下
  聚合块是重复内容
- 音频格式固定请求 pcm（裸 PCM int16 单声道）：流式输出仅 hex 编码，
  pcm 块解码即得裸 PCM 片段，无封装解析（mp3 流式另有帧边界问题）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Optional

import aiohttp

from src.modules.logging import get_logger

# 连接超时 / 单次读超时（秒）：读超时须覆盖服务端首块生成与块间生成的最坏间隔
_CONNECT_TIMEOUT_S = 10.0
_SOCK_READ_TIMEOUT_S = 120.0

# SSE 块定位符；解析时保留尾部数字节防止定位符被块边界截断
_SSE_DATA_PREFIX = b"data:"
_PREFIX_TAIL_KEEP = len(_SSE_DATA_PREFIX) - 1


class MiniMaxTTSApiError(Exception):
    """MiniMax 合成接口不可达 / 业务拒绝。

    aiohttp 的连接类异常字符串携带完整 URL 与底层连接池细节，直接抛出
    会把日志刷成数百行 traceback；本异常只保留对排查有用的简短信息
    （接口地址 + 失败类别），原始异常经 ``__cause__`` 保留供调试器查看。
    """


class MiniMaxTTSClient:
    """MiniMax T2A 流式合成客户端：文本进，PCM 字节片段流出。

    请求参数（模型 / 音色 / 语速等）在构造时一次性绑定，逐句合成只传
    文本——与 api 的"每请求自足描述"形态对应，但绑定来源是引擎配置
    而非会话状态（服务端零会话）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        group_id: str = "",
        model: str,
        voice_id: str,
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: int = 0,
        emotion: str = "",
        sample_rate: int = 24000,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.group_id = group_id.strip()
        self.model = model
        self.voice_id = voice_id
        self.speed = speed
        self.vol = vol
        self.pitch = pitch
        self.emotion = emotion.strip()
        self.sample_rate = sample_rate
        self.logger = get_logger("MiniMaxTTSClient")
        self._session: Optional[aiohttp.ClientSession] = None

    # ----- 请求组装（纯函数，测试直接断言形状）-----

    def _build_url(self) -> str:
        """组装请求 URL：``group_id`` 非空时追加 GroupId 查询参数。"""
        if self.group_id:
            return f"{self.base_url}?GroupId={self.group_id}"
        return self.base_url

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_payload(self, text: str) -> Dict[str, Any]:
        """组装请求体：流式开关 / 声音参数 / 音频参数三个分组。"""
        voice_setting: Dict[str, Any] = {
            "voice_id": self.voice_id,
            "speed": self.speed,
            "vol": self.vol,
            "pitch": self.pitch,
        }
        if self.emotion:
            voice_setting["emotion"] = self.emotion
        return {
            "model": self.model,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "format": "pcm",
                "channel": 1,
            },
        }

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

        流式响应边合成边推，本方法边收边解码：SSE 的 ``data:`` JSON 块
        定位出增量音频的 hex 串，``bytes.fromhex`` 解码即裸 PCM。收尾的
        ``extra_info`` 统计块与任何无音频块跳过不发。HTTP 非 200 与流内
        ``base_resp.status_code`` 非 0 都抛 :class:`MiniMaxTTSApiError`。

        取消语义：调用方取消后本异步生成器随 GC 关闭，aiohttp 的请求
        上下文退出即释放连接（被取消句数量级低，不做显式提前关闭）。
        """
        session = await self._ensure_session()
        try:
            async with session.post(
                self._build_url(), headers=self._build_headers(), json=self._build_payload(text)
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise MiniMaxTTSApiError(f"接口返回 HTTP {response.status}: {body[:200]}")
                async for pcm in self._iter_sse_audio(response.content):
                    yield pcm
        except aiohttp.ClientError as e:
            raise MiniMaxTTSApiError(f"连接 MiniMax 失败（{type(e).__name__}）: {e}") from e

    async def _iter_sse_audio(self, body: Any) -> AsyncIterator[bytes]:
        """从响应体逐块定位 SSE ``data:`` JSON 块并解码出 PCM 片段。

        SSE 无严格行边界保证，按定位符切块：下一个 ``data:`` 出现即为
        前一块的终点；缓冲尾部保留数字节防止定位符本身被传输块截断。
        """
        buffer = b""
        async for chunk in body.iter_any():
            buffer += chunk
            while True:
                start = buffer.find(_SSE_DATA_PREFIX)
                if start == -1:
                    buffer = buffer[-_PREFIX_TAIL_KEEP:]
                    break
                next_start = buffer.find(_SSE_DATA_PREFIX, start + len(_SSE_DATA_PREFIX))
                if next_start == -1:
                    buffer = buffer[start:]
                    break
                block = buffer[start + len(_SSE_DATA_PREFIX) : next_start]
                buffer = buffer[next_start:]
                pcm = self._parse_block(block)
                if pcm:
                    yield pcm
        # 流结束 flush：最后一块没有"下一个 data:"作边界，残留在此处解析
        # （不 flush 会丢每句话的最后一段音频）
        start = buffer.find(_SSE_DATA_PREFIX)
        if start != -1:
            pcm = self._parse_block(buffer[start + len(_SSE_DATA_PREFIX) :])
            if pcm:
                yield pcm

    def _parse_block(self, block: bytes) -> Optional[bytes]:
        """解析单个 SSE 块：业务错误即抛；音频 hex 解码为 PCM；无音频块返回 None。"""
        text = block.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise MiniMaxTTSApiError(f"响应块 JSON 解析失败: {text[:100]}") from e

        # 双层状态：业务错误块的形状是 base_resp 带非 0 码、无音频字段
        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            raise MiniMaxTTSApiError(f"接口业务错误 {status_code}: {base_resp.get('status_msg', '')}")

        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            return None  # extra_info 收尾统计块 / 无音频内容块
        try:
            return bytes.fromhex(audio_hex)
        except ValueError as e:
            raise MiniMaxTTSApiError("音频块 hex 解码失败（响应不是合法 hex 串）") from e


__all__ = ["MiniMaxTTSApiError", "MiniMaxTTSClient"]
