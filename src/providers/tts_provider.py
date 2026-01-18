"""
TTS Provider - Layer 6 Rendering层实现

职责:
- 将ExpressionParameters中的TTS文本转换为语音并播放
- 支持Edge TTS和Omni TTS引擎
- 集成text_cleanup服务
- 集成vts_lip_sync服务
- 通知subtitle_service显示字幕
"""

import asyncio
import tempfile
import time
from typing import Optional, Dict, Any
import numpy as np

from src.core.providers.output_provider import OutputProvider
from src.expression.render_parameters import ExpressionParameters
from src.utils.logger import get_logger

# 检查依赖
DEPENDENCIES_OK = True
try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    DEPENDENCIES_OK = False

try:
    import edge_tts
except ImportError:
    pass  # edge_tts可选，由配置决定使用哪个引擎


class TTSProvider(OutputProvider):
    """
    TTS Provider实现

    核心功能:
    - 支持Edge TTS和Omni TTS两种引擎
    - 集成text_cleanup服务
    - 集成vts_lip_sync服务（口型同步）
    - 通知subtitle_service显示字幕
    - 错误处理和降级方案
    """

    def __init__(self, config: Dict[str, Any], event_bus=None, core=None):
        """
        初始化TTS Provider

        Args:
            config: Provider配置（来自[rendering.outputs.tts]）
            event_bus: EventBus实例（可选）
            core: AmaidesuCore实例（可选，用于访问服务）
        """
        super().__init__(config, event_bus)
        self.core = core
        self.logger = get_logger("TTSProvider")

        # 引擎选择
        self.tts_engine = self.config.get("engine", "edge")  # "edge" or "omni"

        # Edge TTS配置
        self.voice = self.config.get("voice", "zh-CN-XiaoxiaoNeural")
        self.output_device_name = self.config.get("output_device_name", "")
        self.output_device_index = None  # 延迟初始化

        # Omni TTS配置
        self.omni_enabled = self.tts_engine == "omni"
        self.omni_config = self.config.get("omni", {})

        # 音频播放配置
        self.tts_lock = asyncio.Lock()

        # 服务引用（延迟初始化，在setup中获取）
        self.text_cleanup_service = None
        self.vts_lip_sync_service = None
        self.subtitle_service = None

        # 播放状态
        self.is_playing = False
        self.current_task = None

        self.logger.info(f"TTSProvider初始化完成，引擎: {self.tts_engine}, 语音: {self.voice}")

    async def _setup_internal(self):
        """内部设置逻辑"""
        # 查找音频设备索引
        self.output_device_index = self._find_device_index(self.output_device_name)

        # 获取服务引用
        if self.core:
            self.text_cleanup_service = self.core.get_service("text_cleanup")
            self.vts_lip_sync_service = self.core.get_service("vts_lip_sync")
            self.subtitle_service = self.core.get_service("subtitle_service")

            self.logger.info("服务引用获取完成")
            self.logger.info(f"  text_cleanup: {'✓' if self.text_cleanup_service else '✗'}")
            self.logger.info(f"  vts_lip_sync: {'✓' if self.vts_lip_sync_service else '✗'}")
            self.logger.info(f"  subtitle_service: {'✓' if self.subtitle_service else '✗'}")

        # 验证依赖
        if not DEPENDENCIES_OK:
            raise RuntimeError("缺少必要的依赖: sounddevice, soundfile")

        if self.tts_engine == "edge" and "edge_tts" not in globals():
            raise RuntimeError("Edge TTS引擎未安装")

        self.logger.info("TTSProvider设置完成")

    def _find_device_index(self, device_name: Optional[str]) -> Optional[int]:
        """根据设备名称查找设备索引"""
        if "sd" not in globals():
            self.logger.error("sounddevice 库不可用")
            return None

        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    device_name_attr = getattr(device, "name", "")
                    max_output_channels = getattr(device, "max_output_channels", 0)
                    if device_name.lower() in device_name_attr.lower() and max_output_channels > 0:
                        self.logger.info(f"找到输出设备: {device_name_attr} (索引: {i})")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的输出设备，使用默认设备")

            # 使用默认输出设备
            default_device_indices = sd.default.device
            default_index = default_device_indices[1] if default_device_indices[1] != -1 else None
            if default_index is not None:
                default_device_name = getattr(devices[default_index], "name", "Unknown")
                self.logger.info(f"使用默认输出设备: {default_device_name} (索引: {default_index})")
                return default_index

            self.logger.warning("未找到默认输出设备，将由sounddevice选择")
            return None
        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}")
            return None

    async def _render_internal(self, parameters: ExpressionParameters):
        """
        渲染TTS输出

        Args:
            parameters: ExpressionParameters对象
        """
        if not parameters.tts_enabled or not parameters.tts_text:
            self.logger.debug("TTS未启用或文本为空，跳过渲染")
            return

        text = parameters.tts_text
        self.logger.info(f"开始TTS渲染: '{text[:30]}...'")

        # 1. 文本清理（如果服务可用）
        final_text = text
        if self.text_cleanup_service:
            try:
                cleaned = await self.text_cleanup_service.clean_text(text)
                if cleaned:
                    self.logger.info(f"文本已清理: '{cleaned[:30]}...'")
                    final_text = cleaned
            except Exception as e:
                self.logger.error(f"文本清理失败: {e}，使用原始文本")

        if not final_text:
            self.logger.warning("清理后文本为空，跳过TTS")
            return

        # 2. 执行TTS
        await self._speak(final_text, parameters)

    async def _speak(self, text: str, parameters: ExpressionParameters):
        """
        执行TTS合成和播放

        Args:
            text: 要合成的文本
            parameters: ExpressionParameters对象
        """
        async with self.tts_lock:
            tmp_filename = None
            try:
                # 2.1 合成语音
                if self.tts_engine == "edge":
                    audio_array, samplerate = await self._edge_tts_synthesize(text)
                elif self.tts_engine == "omni" and self.omni_enabled:
                    audio_array, samplerate = await self._omni_tts_synthesize(text)
                else:
                    raise RuntimeError(f"不支持的TTS引擎: {self.tts_engine}")

                # 2.2 计算音频时长
                duration_seconds = len(audio_array) / samplerate if samplerate > 0 else 3.0
                self.logger.info(f"音频时长: {duration_seconds:.3f}秒")

                # 2.3 通知字幕服务
                if self.subtitle_service and duration_seconds > 0:
                    try:
                        asyncio.create_task(self.subtitle_service.record_speech(text, duration_seconds))
                    except Exception as e:
                        self.logger.error(f"调用subtitle_service失败: {e}")

                # 2.4 启动口型同步（如果服务可用）
                if self.vts_lip_sync_service:
                    try:
                        await self.vts_lip_sync_service.start_lip_sync_session(text)
                    except Exception as e:
                        self.logger.error(f"启动口型同步失败: {e}")

                # 2.5 播放音频
                self.logger.info("开始播放音频...")
                await self._play_audio(audio_array, samplerate, self.output_device_index)

            except Exception as e:
                self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
                raise RuntimeError(f"TTS渲染失败: {e}") from e

            finally:
                # 清理临时文件
                if tmp_filename and tmp_filename.startswith(tempfile.gettempdir()):
                    try:
                        import os

                        os.remove(tmp_filename)
                        self.logger.debug(f"已删除临时文件: {tmp_filename}")
                    except Exception as e_rem:
                        self.logger.warning(f"删除临时文件失败: {e_rem}")

                # 停止口型同步
                if self.vts_lip_sync_service:
                    try:
                        await self.vts_lip_sync_service.stop_lip_sync_session()
                    except Exception as e:
                        self.logger.debug(f"停止口型同步失败: {e}")

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

        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name

            # 合成语音
            communicate = edge_tts.Communicate(text, self.voice)
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            self.logger.info(f"Edge TTS合成完成: {tmp_filename}")

            # 读取音频
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

            return audio_array, samplerate

        except Exception as e:
            self.logger.error(f"Edge TTS合成失败: {e}", exc_info=True)
            # 返回静音作为降级方案
            return np.zeros(44100, dtype=np.float32), 16000

    async def _omni_tts_synthesize(self, text: str):
        """
        使用Omni TTS合成语音

        Args:
            text: 要合成的文本

        Returns:
            audio_array, samplerate
        """
        self.logger.info(f"Omni TTS合成: {text[:30]}...")

        # TODO: 集成实际的Omni TTS API
        # 当前返回静音作为占位符
        self.logger.warning("Omni TTS引擎集成待实现")
        return np.zeros(44100, dtype=np.float32), 16000

    async def _play_audio(self, audio_array: np.ndarray, samplerate: int, device_index: Optional[int]):
        """
        播放音频

        Args:
            audio_array: 音频数据数组
            samplerate: 采样率
            device_index: 设备索引
        """
        self.logger.info(f"开始播放音频 (设备索引: {device_index})...")

        try:
            # 停止现有播放
            sd.stop()

            # 播放音频
            sd.play(audio_array, samplerate=samplerate, device=device_index)

            # 计算播放时长并等待
            duration = len(audio_array) / samplerate
            wait_time = duration + 0.3  # 额外等待0.3秒
            self.logger.debug(f"等待音频播放: {wait_time:.3f} 秒")

            await asyncio.sleep(wait_time)

            # 确保播放停止
            sd.stop()

            self.logger.info("音频播放完成")

        except Exception as e:
            self.logger.error(f"音频播放失败: {e}", exc_info=True)
            raise e

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("TTSProvider清理中...")

        # 停止所有播放
        try:
            sd.stop()
        except Exception as e:
            self.logger.debug(f"停止播放失败: {e}")

        self.text_cleanup_service = None
        self.vts_lip_sync_service = None
        self.subtitle_service = None

        self.logger.info("TTSProvider清理完成")
