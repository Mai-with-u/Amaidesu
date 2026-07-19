"""
GPTSoVITS Handler - Output 阶段: 渲染输出实现

职责:
- 使用GPT-SoVITS引擎进行文本转语音
- 流式TTS和音频播放
- 参考音频管理
- 音频设备管理
"""

import re
from collections import deque
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    pass

import numpy as np
from pydantic import Field

from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.streaming.audio_stream_channel import AudioStreamChannel
from src.modules.tts import GPTSoVITSClient
from src.modules.types import Intent

from ..base import AudioHandlerBase

# 导入工具函数
from ..utils.wav_decoder import decode_wav_chunk

# --- 音频流参数 ---
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024
SAMPLE_SIZE = DTYPE().itemsize
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE


# GPT-SoVITS 中文归一化器(RE_NUMBER 用 \d 匹配 Unicode 数字,DIGITS 字典只含 ASCII)
# 对 emoji、颜文字、泰文/阿拉伯文/全角数字等会抛 KeyError。黑名单追不齐,改用白名单。
# 白名单:中文/英文/ASCII 数字/常用标点/空白;其余一律移除。
_TTS_UNSAFE_CHARS = re.compile(
    r"[^\u4e00-\u9fff"  # 中文 CJK 统一汉字
    r"\u3000-\u303f"  # CJK 标点(。 、 〈〉等)
    r"\uff01-\uff0f"  # 全角标点 !"#$%&'()*+,-./
    r"\uff1a-\uff20"  # 全角标点 :;<=>?@
    r"\uff3b-\uff40"  # 全角标点 [\]^_`
    r"\uff5b-\uff5e"  # 全角标点 {|}~
    r"a-zA-Z0-9\s"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]"  # ASCII 标点
)


def _sanitize_text_for_tts(text: str) -> str:
    """白名单清洗:只保留中英文、ASCII 数字、常用标点、空白。

    用于规避 GPT-SoVITS 归一化器对 emoji/颜文字/稀有 Unicode 抛 KeyError 的服务端 bug。
    """
    return _TTS_UNSAFE_CHARS.sub("", text)


@handler("gptsovits")
class GPTSoVITSHandler(AudioHandlerBase):
    """
    GPTSoVITS Handler实现

    核心功能:
    - 使用GPT-SoVITS API进行文本转语音
    - 流式TTS和音频播放
    - 音频设备管理
    """

    class ConfigSchema(BaseConfig):
        """GPT-SoVITS输出Handler配置"""

        type: str = "gptsovits"

        # API配置
        host: str = Field(default="127.0.0.1", description="API主机地址")
        port: int = Field(default=9880, ge=1, le=65535, description="API端口")

        # 参考音频配置
        ref_audio_path: str = Field(default="", description="参考音频路径")
        prompt_text: str = Field(default="", description="提示文本")

        # TTS参数
        text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", description="文本语言")
        prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", description="提示语言")
        top_k: int = Field(default=20, ge=1, le=100, description="Top-K采样")
        top_p: float = Field(default=0.6, ge=0.0, le=1.0, description="Top-P采样")
        temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="温度参数")
        speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, description="语速因子")
        streaming_mode: bool = Field(default=True, description="是否启用流式模式")
        media_type: str = Field(default="wav", pattern=r"^(wav|mp3|ogg)$", description="媒体类型")
        text_split_method: str = Field(
            default="cut5",
            pattern=r"^(cut0|cut1|cut2|cut3|cut4|cut5)$",
            description="文本分割方法(api_v2.py 格式)",
        )
        batch_size: int = Field(default=1, ge=1, le=10, description="批处理大小")
        batch_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="批处理阈值")
        repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0, description="重复惩罚")
        sample_steps: int = Field(default=10, ge=1, le=50, description="采样步数")
        super_sampling: bool = Field(default=True, description="是否启用超采样")

        # 音频输出配置
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: AudioStreamChannel,
    ):
        """
        初始化GPTSoVITS Handler

        Args:
            config: Handler配置（来自[handlers.gptsovits]）
            event_bus: EventBus实例
            audio_stream_channel: AudioStreamChannel实例
        """
        super().__init__(config, event_bus, audio_stream_channel)

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema.from_dict(config)

        # GPT-SoVITS API配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port

        # 参考音频配置
        self.ref_audio_path = self.typed_config.ref_audio_path
        self.prompt_text = self.typed_config.prompt_text

        # TTS参数
        self.text_language = self.typed_config.text_language
        self.prompt_language = self.typed_config.prompt_language
        self.top_k = self.typed_config.top_k
        self.top_p = self.typed_config.top_p
        self.temperature = self.typed_config.temperature
        self.speed_factor = self.typed_config.speed_factor
        self.streaming_mode = self.typed_config.streaming_mode
        self.media_type = self.typed_config.media_type
        self.text_split_method = self.typed_config.text_split_method
        self.batch_size = self.typed_config.batch_size
        self.batch_threshold = self.typed_config.batch_threshold
        self.repetition_penalty = self.typed_config.repetition_penalty
        self.sample_steps = self.typed_config.sample_steps
        self.super_sampling = self.typed_config.super_sampling

        # 音频输出配置
        self.sample_rate = self.typed_config.sample_rate

        # 音频缓冲区
        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        # 客户端（在 init 中初始化）
        self.tts_client: Optional[GPTSoVITSClient] = None

        self.logger.info("GPTSoVITSHandler初始化完成")

    async def init(self):
        """初始化逻辑"""
        # 初始化TTS客户端
        self.tts_client = GPTSoVITSClient(self.host, self.port)
        self.tts_client.initialize()

        # 本地缓存参考音频(发送 TTS 请求时自动带上)
        if self.ref_audio_path and self.prompt_text:
            self.tts_client.set_refer_audio(self.ref_audio_path, self.prompt_text)

        # 初始化音频设备管理器
        self._setup_audio_device(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            device_name=self.typed_config.output_device_name,
        )

        # 加载默认预设
        self.tts_client.load_preset("default")

        # 订阅事件
        await self._subscribe_output_events()

        self.logger.info("GPTSoVITSHandler设置完成")

    async def cleanup(self):
        """清理资源"""
        self.logger.info("GPTSoVITSHandler清理中...")

        # 取消事件订阅
        await self._unsubscribe_output_events()

        # 停止音频流播放
        if self.audio_manager:
            self.audio_manager.stop_stream()

        # 清空缓冲区
        self.input_pcm_queue.clear()
        self.audio_data_queue.clear()

        self.logger.info("GPTSoVITSHandler清理完成")

    async def handle(self, intent: Intent):
        """
        执行 TTS 输出

        覆盖父类 handle() 因为 GPT-SoVITS 需要在加锁前做文本清洗。
        Args:
            intent: Intent对象，从 speech 获取 TTS 文本
        """
        text = intent.speech

        if not text or not text.strip():
            self.logger.debug("TTS文本为空，跳过渲染")
            await self._emit_completed(intent, success=True)
            return

        original_text = text.strip()
        self.logger.debug(f"准备TTS: '{original_text[:50]}...'")

        # 文本清洗:移除 GPT-SoVITS 不支持的字符(emoji、泰语数字等),
        # 否则会触发中文归一化器的 KeyError
        final_text = _sanitize_text_for_tts(original_text)
        if final_text != original_text:
            self.logger.debug(f"文本清洗: 移除了 {len(original_text) - len(final_text)} 个不支持字符")
        if not final_text.strip():
            self.logger.debug("清洗后文本为空，跳过渲染")
            await self._emit_completed(intent, success=True)
            return

        success = True
        try:
            async with self.tts_lock:
                await self._notify_audio_start(final_text)

                # 执行TTS（流式）
                audio_stream = self.tts_client.tts_stream(
                    text=final_text,
                    text_lang=self.text_language,
                    prompt_lang=self.prompt_language,
                    top_k=self.top_k,
                    top_p=self.top_p,
                    temperature=self.temperature,
                    speed_factor=self.speed_factor,
                    text_split_method=self.text_split_method,
                    batch_size=self.batch_size,
                    batch_threshold=self.batch_threshold,
                    repetition_penalty=self.repetition_penalty,
                    sample_steps=self.sample_steps,
                    super_sampling=self.super_sampling,
                    media_type=self.media_type,
                )

                # 启动流式播放(边收边播,不等全部合成完)
                self.audio_manager.start_stream()

                chunk_index = 0
                async for chunk in self._process_audio_stream(audio_stream):
                    if chunk is not None:
                        # 发布到 AudioStreamChannel 供 VTS/Warudo 口型同步
                        await self._publish_chunk(
                            data=chunk.tobytes(),
                            sample_rate=self.sample_rate,
                            channels=CHANNELS,
                            sequence=chunk_index,
                        )

                        # 写入扬声器立即播放(首音延迟 ~2-3s)
                        self.audio_manager.write_chunk(chunk)
                        chunk_index += 1

                self.audio_manager.stop_stream()

                await self._notify_audio_end(final_text)

            self.logger.debug(f"TTS播放完成: '{final_text[:30]}...'")
            self.render_count += 1

        except Exception as e:
            success = False
            self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise
        finally:
            await self._emit_completed(intent, success=success)

    async def _process_audio_stream(self, audio_stream):
        """
        处理音频流

        Args:
            audio_stream: 音频流迭代器（同步或异步）

        Yields:
            numpy.ndarray: 音频数据块
        """
        try:
            # 检查是否是异步迭代器
            if hasattr(audio_stream, "__aiter__"):
                # 异步迭代器
                async for chunk in audio_stream:
                    if not chunk:
                        self.logger.debug("收到空音频块，跳过")
                        continue

                    # 解析WAV数据
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
            else:
                # 同步迭代器
                for chunk in audio_stream:
                    if not chunk:
                        self.logger.debug("收到空音频块，跳过")
                        continue

                    # 解析WAV数据
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk

        except Exception as e:
            self.logger.error(f"处理音频流失败: {e}", exc_info=True)
            raise
