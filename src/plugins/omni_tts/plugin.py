# OmniTTS Plugin - 新Plugin架构实现

import asyncio
from typing import Dict, Any, List, Optional

from src.core.plugin import Plugin
from src.core.providers.output_provider import OutputProvider
from src.core.providers.base import RenderParameters
from src.utils.logger import get_logger


class OmniTTSOutputProvider(OutputProvider):
    """
    Omni TTS OutputProvider

    使用阿里云Qwen-Omni大模型进行语音合成。
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.logger = get_logger("OmniTTSOutputProvider")

        # 检查依赖
        try:
            import aiohttp
            import sounddevice as sd
            import soundfile as sf
        except ImportError as e:
            self.logger.error(f"依赖缺失: {e}")
            raise ImportError("请安装必要依赖: pip install aiohttp sounddevice soundfile")

        # 初始化 OmniTTS
        try:
            from .omni_tts import OmniTTS

            self.omni_tts = OmniTTS(
                api_key=config.get("api_key") or self._get_api_key_from_env(),
                model_name=config.get("model_name", "qwen2.5-omni-7b"),
                voice=config.get("voice", "Chelsie"),
                format=config.get("format", "wav"),
                base_url=config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                enable_post_processing=config.get("post_processing", {}).get("enabled", False),
                volume_reduction_db=config.get("post_processing", {}).get("volume_reduction", 0.0),
                noise_level=config.get("post_processing", {}).get("noise_level", 0.0),
                blow_up_probability=config.get("blow_up_probability", 0.0),
                blow_up_texts=config.get("blow_up_texts", []),
            )
        except ImportError as e:
            self.logger.error(f"无法导入 OmniTTS: {e}")
            raise

        # 音频设备配置
        self.output_device_name = config.get("output_device_name")
        self.output_device_index = self._find_device_index(self.output_device_name)

        # UDP 广播配置
        self.udp_enabled = config.get("udp_broadcast", {}).get("enable", False)
        self.udp_socket = None
        self.udp_dest = None

        if self.udp_enabled:
            self._setup_udp_socket(config.get("udp_broadcast", {}))

    def _get_api_key_from_env(self) -> Optional[str]:
        """从环境变量获取API密钥"""
        import os

        return os.environ.get("DASHSCOPE_API_KEY")

    def _find_device_index(self, device_name: Optional[str]) -> Optional[int]:
        """根据设备名称查找设备索引"""
        if not device_name:
            return None

        try:
            import sounddevice as sd

            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if device_name.lower() in device["name"].lower() and device["max_output_channels"] > 0:
                    self.logger.info(f"找到音频设备 '{device['name']}'，索引: {i}")
                    return i
            self.logger.warning(f"未找到音频设备 '{device_name}'")
            return None
        except Exception as e:
            self.logger.error(f"查找音频设备失败: {e}")
            return None

    def _setup_udp_socket(self, udp_config: Dict[str, Any]):
        """设置UDP广播"""
        try:
            import socket

            host = udp_config.get("host", "127.0.0.1")
            port = udp_config.get("port", 9999)
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_dest = (host, port)
            self.logger.info(f"UDP广播已启用，目标: {self.udp_dest}")
        except Exception as e:
            self.logger.error(f"设置UDP socket失败: {e}")
            self.udp_socket = None
            self.udp_enabled = False

    async def _setup_internal(self):
        """内部设置逻辑"""
        self.logger.info("OmniTTSOutputProvider设置完成")

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染TTS输出

        Args:
            parameters: RenderParameters对象
        """
        if not parameters.tts_enabled or not parameters.tts_text:
            self.logger.debug("TTS未启用或文本为空，跳过渲染")
            return

        text = parameters.tts_text

        try:
            # UDP广播
            if self.udp_enabled and self.udp_socket and self.udp_dest:
                self._broadcast_text(text)

            # 生成音频
            audio_data = await self.omni_tts.generate_audio(text)

            # 播放音频
            await self._speak_audio(text, audio_data)

            self.logger.info(f"TTS渲染完成: '{text[:30]}...'")

        except Exception as e:
            self.logger.error(f"OmniTTS渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"OmniTTS渲染失败: {e}") from e

    def _broadcast_text(self, text: str):
        """通过UDP发送文本"""
        if self.udp_socket and self.udp_dest:
            try:
                message_bytes = text.encode("utf-8")
                self.udp_socket.sendto(message_bytes, self.udp_dest)
                self.logger.debug(f"已发送文本到UDP监听器: {self.udp_dest}")
            except Exception as e:
                self.logger.warning(f"UDP广播失败: {e}")

    async def _speak_audio(self, text: str, audio_data: bytes):
        """播放音频数据"""
        import os
        import tempfile
        import asyncio

        try:
            import soundfile as sf
            import sounddevice as sd
            import numpy as np
            import time
        except ImportError as e:
            self.logger.error(f"缺少音频库: {e}")
            raise

        tmp_filename = None
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                tmp_filename = tmp_file.name
                tmp_file.write(audio_data)
            self.logger.debug(f"音频已保存到临时文件: {tmp_filename}")

            # 读取音频并计算时长
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

            duration_seconds = len(audio_array) / samplerate if samplerate > 0 else 0
            self.logger.info(f"音频时长: {duration_seconds:.3f} 秒")

            # 播放音频
            self.logger.info(f"开始播放音频 (设备索引: {self.output_device_index})...")

            sd.stop()  # 停止所有现有播放

            actual_start_time = time.time()
            sd.play(audio_array, samplerate=samplerate, device=self.output_device_index)

            # 等待播放完成
            wait_time = duration_seconds + 0.3
            await asyncio.sleep(wait_time)

            sd.stop()

            actual_end_time = time.time()
            self.logger.info(f"音频播放完成，总耗时: {actual_end_time - actual_start_time:.3f} 秒")

        except Exception as e:
            self.logger.error(f"音频播放失败: {e}", exc_info=True)
            raise
        finally:
            if tmp_filename and os.path.exists(tmp_filename):
                try:
                    os.remove(tmp_filename)
                except Exception as e:
                    self.logger.warning(f"删除临时文件失败: {e}")

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("OmniTTSOutputProvider清理中...")

        if self.udp_socket:
            try:
                self.udp_socket.close()
            except Exception as e:
                self.logger.error(f"关闭UDP socket失败: {e}")

        self.logger.info("OmniTTSOutputProvider清理完成")


class OmniTTSPlugin:
    """
    Omni TTS插件 - 使用阿里云Qwen-Omni大模型进行语音合成

    迁移到新的Plugin架构，包装OmniTTSOutputProvider。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._providers: List[OutputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("OmniTTSPlugin 在配置中已禁用。")
            return

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        # 创建Provider
        try:
            provider = OmniTTSOutputProvider(self.config)
            self._providers.append(provider)
            self.logger.info("OmniTTSOutputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建Provider失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 OmniTTSPlugin...")

        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理Provider时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("OmniTTSPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "OmniTTS",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Omni TTS插件，使用阿里云Qwen-Omni大模型进行语音合成",
            "category": "output",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = OmniTTSPlugin
