"""
Voicebox Handler - Output 阶段: 渲染输出实现

职责:
- 使用 Voicebox（本地语音桌面应用）HTTP API 进行文本转语音
- 异步调用：POST /generate → SSE 轮询状态 → GET /audio/{id} 获取音频
- 支持 profile_id 选择音色
- 音频设备管理
"""

import json
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    pass

import aiohttp
import numpy as np
from pydantic import Field

from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.streaming.audio_stream_channel import AudioStreamChannel

from ..base import AudioHandlerBase
from ..utils.wav_decoder import extract_pcm_from_wav

CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024


@handler("voicebox")
class VoiceboxHandler(AudioHandlerBase):
    """
    Voicebox Handler 实现

    核心功能:
    - 通过 Voicebox REST API 进行文本转语音
    - Voicebox API 流程：POST /generate → SSE 状态轮询 → GET /audio/{id}
    - 支持 profile_id 选择音色
    - 音频设备管理
    """

    class ConfigSchema(BaseConfig):
        type: str = "voicebox"

        host: str = Field(default="127.0.0.1", description="Voicebox API 主机地址")
        port: int = Field(default=17493, ge=1, le=65535, description="Voicebox API 端口")

        profile_id: Optional[str] = Field(default=None, description="语音 profile ID（Voicebox 中克隆的音色 ID，必填）")
        language: str = Field(default="zh", description="语言代码（如 zh/en/ja）")

        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=24000, ge=8000, le=48000, description="输出采样率")

        generation_timeout_ms: int = Field(
            default=120000, ge=5000, le=300000, description="生成超时（毫秒，首次需加载模型可能较慢）"
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: AudioStreamChannel,
    ):
        super().__init__(config, event_bus, audio_stream_channel)

        self.typed_config = self.ConfigSchema.from_dict(config)

        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.base_url = f"http://{self.host}:{self.port}"
        self.profile_id = self.typed_config.profile_id
        self.language = self.typed_config.language
        self.sample_rate = self.typed_config.sample_rate
        self.generation_timeout = self.typed_config.generation_timeout_ms / 1000

        self.render_count = 0
        self.error_count = 0
        self._session: Optional[aiohttp.ClientSession] = None

        self.logger.info(f"VoiceboxHandler 初始化完成，API: {self.base_url}")

    async def init(self):
        """初始化 Handler"""
        self._session = aiohttp.ClientSession()

        self._setup_audio_device(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            device_name=self.typed_config.output_device_name,
        )

        # 健康检查
        try:
            async with self._session.get(f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=5)):
                self.logger.info("Voicebox 服务连接成功")
        except Exception as e:
            self.logger.warning(f"Voicebox 健康检查失败: {e}")

        await self._subscribe_output_events()
        self.logger.info("VoiceboxHandler 设置完成")

    async def cleanup(self):
        """清理资源"""
        self.logger.info("VoiceboxHandler 清理中...")

        await self._unsubscribe_output_events()
        if self.audio_manager:
            self.audio_manager.stop_audio()
        if self._session and not self._session.closed:
            await self._session.close()

        self.logger.info("VoiceboxHandler 清理完成")

    async def _synthesize(self, text: str):
        """
        核心合成逻辑：异步调用 Voicebox API。

        Voicebox API 流程:
          1. POST /generate → 返回 generation_id
          2. GET /generate/{id}/status（SSE）→ 解析流直到 status="completed"
          3. GET /audio/{id} → 下载音频
        """
        if not self._session or self._session.closed:
            raise RuntimeError("Voicebox HTTP 会话未初始化")
        if not self.profile_id:
            raise RuntimeError("未配置 profile_id，请先在 Voicebox 中克隆音色并填写配置")

        generation_id = await self._create_generation(text)
        await self._wait_for_completion(generation_id)
        audio_data = await self._download_audio(generation_id)

        # 解析 WAV 并发布音频块
        pcm_data = extract_pcm_from_wav(audio_data)
        audio_array = np.frombuffer(pcm_data, dtype=DTYPE)

        if len(audio_array) == 0:
            self.logger.warning("Voicebox 返回空音频，跳过播放")
            return

        chunk_size = BLOCKSIZE * CHANNELS
        chunk_index = 0
        for i in range(0, len(audio_array), chunk_size):
            chunk_data = audio_array[i : i + chunk_size]
            await self._publish_chunk(
                data=chunk_data.tobytes(),
                sample_rate=self.sample_rate,
                channels=CHANNELS,
                sequence=chunk_index,
            )
            chunk_index += 1

        await self.audio_manager.play_audio(audio_array)

    async def _create_generation(self, text: str) -> str:
        """POST /generate 创建语音生成任务，返回 generation_id。"""
        payload = {
            "text": text,
            "profile_id": self.profile_id,
            "language": self.language,
        }

        async with self._session.post(
            f"{self.base_url}/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox /generate 失败 (HTTP {resp.status}): {body[:200]}")

            data = await resp.json()
            gen_id = data.get("id")
            if not gen_id:
                raise RuntimeError(f"Voicebox /generate 返回中没有 id: {data}")
            self.logger.debug(f"创建生成任务: id={gen_id}")
            return gen_id

    async def _wait_for_completion(self, generation_id: str):
        """
        通过 SSE 读取生成状态直到 completed 或超时。

        GET /generate/{id}/status 返回 SSE 事件流:
          data: {"id": "...", "status": "generating", ...}
          data: {"id": "...", "status": "completed", "duration": 3.04, ...}
        """
        url = f"{self.base_url}/generate/{generation_id}/status"
        timeout = aiohttp.ClientTimeout(total=self.generation_timeout + 10)

        async with self._session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox 状态轮询失败 (HTTP {resp.status}): {body[:200]}")

            async for line_bytes in resp.content:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue

                event_data = line[len("data: ") :]
                try:
                    event = json.loads(event_data)
                except json.JSONDecodeError:
                    continue

                status = event.get("status")
                if status == "completed":
                    duration = event.get("duration", 0)
                    self.logger.debug(f"生成完成: id={generation_id}, 耗时={duration}s")
                    return
                elif status == "failed":
                    err = event.get("error", "未知错误")
                    raise RuntimeError(f"Voicebox 生成失败: {err}")

    async def _download_audio(self, generation_id: str) -> bytes:
        """GET /audio/{id} 下载生成的 WAV 音频。"""
        url = f"{self.base_url}/audio/{generation_id}"
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox 音频下载失败 (HTTP {resp.status}): {body[:200]}")
            data = await resp.read()
            self.logger.debug(f"下载音频: id={generation_id}, 大小={len(data)} 字节")
            return data

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._session is not None and not self._session.closed,
            "render_count": self.render_count,
            "error_count": self.error_count,
        }
