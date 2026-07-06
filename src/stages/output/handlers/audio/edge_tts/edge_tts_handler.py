"""
EdgeTTS Handler - Output 阶段: 渲染输出实现

职责:
- 使用Edge TTS引擎进行文本转语音并播放
"""

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.streaming.audio_stream_channel import AudioStreamChannel

if TYPE_CHECKING:
    pass

from ..base import AudioHandlerBase

# 检查依赖
DEPENDENCIES_OK = True
try:
    import soundfile as sf
except ImportError:
    DEPENDENCIES_OK = False

try:
    import edge_tts
except ImportError:
    pass  # edge_tts可选，由配置决定使用哪个引擎


@handler("edge_tts")
class EdgeTTSHandler(AudioHandlerBase):
    """
    EdgeTTS Handler实现

    核心功能:
    - 使用Edge TTS引擎进行文本转语音
    - 音频播放和设备管理
    """

    class ConfigSchema(BaseConfig):
        """EdgeTTS输出Handler配置"""

        type: str = "edge_tts"

        # Edge TTS配置
        voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Edge TTS语音")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: AudioStreamChannel,
    ):
        """
        初始化EdgeTTS Handler

        Args:
            config: Handler配置（来自[handlers.edge_tts]）
            event_bus: EventBus实例
            audio_stream_channel: AudioStreamChannel实例
        """
        super().__init__(config, event_bus, audio_stream_channel)

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema.from_dict(config)

        # Edge TTS配置
        self.voice = self.typed_config.voice
        self.output_device_name = self.typed_config.output_device_name or ""

        self.logger.info(f"EdgeTTSHandler初始化完成，语音: {self.voice}")

    async def init(self):
        """初始化 Handler"""
        # 验证依赖
        if not DEPENDENCIES_OK:
            raise RuntimeError("缺少必要的依赖: soundfile")

        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS引擎未安装")

        # 初始化音频设备管理器
        self._setup_audio_device(
            sample_rate=48000,  # Edge TTS 默认采样率
            channels=1,
            dtype=np.float32,
            device_name=self.output_device_name or None,
        )

        # 订阅事件
        await self._subscribe_output_events()

        self.logger.info("EdgeTTSHandler启动完成")

    async def _synthesize(self, text: str):
        """
        执行TTS合成、发布和播放（由父类 handle() 模板方法调用，
        已在 tts_lock 保护内且已发送 notify_start）。
        """
        self.logger.debug(f"开始TTS渲染: '{text[:30]}...'")

        tmp_filename = None
        try:
            # 合成语音
            audio_array, samplerate = await self._edge_tts_synthesize(text)

            # 计算音频时长
            duration_seconds = len(audio_array) / samplerate if samplerate > 0 else 3.0
            self.logger.debug(f"音频时长: {duration_seconds:.3f}秒")

            # 发布音频块
            chunk_size = 1024
            for i in range(0, len(audio_array), chunk_size):
                chunk_data = audio_array[i : i + chunk_size]
                await self._publish_chunk(
                    data=chunk_data.tobytes(),
                    sample_rate=samplerate,
                    channels=1,
                    sequence=i // chunk_size,
                )

            # 播放音频
            self.logger.debug("开始播放音频...")
            await self.audio_manager.play_audio(audio_array)

        except Exception as e:
            self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"TTS渲染失败: {e}") from e

        finally:
            # 清理临时文件
            if tmp_filename and tmp_filename.startswith(tempfile.gettempdir()):
                try:
                    os.remove(tmp_filename)
                    self.logger.debug(f"已删除临时文件: {tmp_filename}")
                except Exception as e_rem:
                    self.logger.warning(f"删除临时文件失败: {e_rem}")

    async def _edge_tts_synthesize(self, text: str):
        """
        使用Edge TTS合成语音

        Args:
            text: 要合成的文本

        Returns:
            audio_array, samplerate
        """
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS未安装")

        tmp_filename = None
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name

            # 合成语音
            communicate = edge_tts.Communicate(text, self.voice)
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            self.logger.debug(f"Edge TTS合成完成: {tmp_filename}")

            # 读取音频
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

            return audio_array, samplerate

        except Exception as e:
            self.logger.error(f"Edge TTS合成失败: {e}", exc_info=True)
            # 返回静音作为降级方案
            return np.zeros(44100, dtype=np.float32), 16000

        finally:
            if tmp_filename:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass

    async def cleanup(self):
        """清理资源"""
        self.logger.info("EdgeTTSHandler停止中...")

        # 取消事件订阅
        await self._unsubscribe_output_events()

        # 停止所有播放
        if self.audio_manager:
            self.audio_manager.stop_audio()

        self.logger.info("EdgeTTSHandler停止完成")
