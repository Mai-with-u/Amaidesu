"""
音频设备管理器

提供音频设备管理功能：
- 设备查询
- 设备选择
- 音频流播放
"""

import asyncio
from typing import Any, Dict, List, Optional

import numpy as np

from src.modules.logging import get_logger

# sounddevice 是可选依赖：缺失时保持模块可加载，相关 API 走优雅降级路径
DEPENDENCIES_OK = False
try:
    import sounddevice as sd

    DEPENDENCIES_OK = True
except ImportError:
    pass


class AudioDeviceManager:
    """
    音频设备管理器

    管理音频输出设备和音频播放。可挂载一个 ``AudioSink`` 分接（全量与
    流式两种播法都会把音频复制递入；分接是装饰性路径，任何 sink 异常
    只记日志，不影响播放）。
    """

    def __init__(
        self,
        sample_rate: int = 32000,
        channels: int = 1,
        dtype: type = np.int16,
        sink: Optional[Any] = None,
    ) -> None:
        """
        初始化音频设备管理器

        Args:
            sample_rate: 采样率
            channels: 声道数
            dtype: 数据类型
            sink: 可选音频分接接收方（``AudioSink`` 协议形状；口型分析等
                消费者经构造链注入）
        """
        self.logger = get_logger("AudioDeviceManager")
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._sink = sink

        # 设备配置
        self.output_device_name: Optional[str] = None
        self.output_device_index: Optional[int] = None

        # 播放状态（_stream 由 start_stream 创建；未启动时 write/stop 静默跳过）
        self.is_playing = False
        self.current_stream: Optional[Any] = None
        self._stream: Optional[Any] = None

        if not DEPENDENCIES_OK:
            self.logger.error("音频依赖缺失，请安装: pip install sounddevice soundfile")

    # -----分接（复制音频给注入的 sink；装饰性路径 fail-soft）-----

    def _sink_start(self, utterance_id: str = "") -> None:
        if self._sink is None:
            return
        try:
            self._sink.start(utterance_id)
        except Exception as e:
            self.logger.warning(f"音频分接 start 异常（已忽略）: {e}")

    def _sink_feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        if self._sink is None:
            return
        try:
            self._sink.feed(chunk, sample_rate)
        except Exception as e:
            self.logger.warning(f"音频分接 feed 异常（已忽略）: {e}")

    def _sink_stop(self) -> None:
        if self._sink is None:
            return
        try:
            self._sink.stop()
        except Exception as e:
            self.logger.warning(f"音频分接 stop 异常（已忽略）: {e}")

    def find_device_index(self, device_name: Optional[str]) -> Optional[int]:
        """
        根据设备名称查找设备索引

        Args:
            device_name: 设备名称

        Returns:
            设备索引，如果未找到则返回 None
        """
        if not DEPENDENCIES_OK:
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
                default_device_name_attr = getattr(devices[default_index], "name", "Unknown")
                self.logger.info(f"使用默认输出设备: {default_device_name_attr} (索引: {default_index})")
                return default_index

            self.logger.warning("未找到默认输出设备，将由 sounddevice 选择")
            return None
        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}")
            return None

    def set_output_device(self, device_name: Optional[str] = None, device_index: Optional[int] = None) -> None:
        """
        设置输出设备

        Args:
            device_name: 设备名称
            device_index: 设备索引
        """
        if device_index is not None:
            self.output_device_index = device_index
            self.output_device_name = None
            self.logger.info(f"设置输出设备索引: {device_index}")
        elif device_name:
            self.output_device_name = device_name
            self.output_device_index = self.find_device_index(device_name)
            self.logger.info(f"设置输出设备名称: {device_name}")

    def list_output_devices(self) -> List[Dict[str, Any]]:
        """
        列出所有输出设备

        Returns:
            设备信息列表
        """
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return []

        try:
            devices = sd.query_devices()
            output_devices = []

            for i, device in enumerate(devices):
                max_output_channels = getattr(device, "max_output_channels", 0)
                if max_output_channels > 0:
                    output_devices.append(
                        {
                            "index": i,
                            "name": getattr(device, "name", "Unknown"),
                            "max_output_channels": max_output_channels,
                            "default_samplerate": getattr(device, "default_samplerate", 0),
                        }
                    )

            return output_devices
        except Exception as e:
            self.logger.error(f"列出输出设备时出错: {e}")
            return []

    async def play_audio(
        self,
        audio_array: np.ndarray,
        samplerate: Optional[int] = None,
        device_index: Optional[int] = None,
    ) -> None:
        """
        播放音频

        Args:
            audio_array: 音频数据数组
            samplerate: 采样率（默认使用 self.sample_rate）
            device_index: 设备索引（默认使用 self.output_device_index）
        """
        if not DEPENDENCIES_OK:
            self.logger.error("音频依赖缺失")
            raise RuntimeError("音频依赖缺失")

        if samplerate is None:
            samplerate = self.sample_rate
        if device_index is None:
            device_index = self.output_device_index

        self.logger.debug(f"开始播放音频 (设备索引: {device_index}, 采样率: {samplerate})...")

        # 分接：开播同步开 session，整段音频一次递入（全量播法）
        self._sink_start()
        self._sink_feed(audio_array, samplerate)

        try:
            # 停止现有播放
            sd.stop()

            # 播放音频
            sd.play(audio_array, samplerate=samplerate, device=device_index)
            self.is_playing = True

            # 计算播放时长并等待
            duration = len(audio_array) / samplerate
            wait_time = duration + 0.3  # 额外等待 0.3 秒
            self.logger.debug(f"等待音频播放: {wait_time:.3f} 秒")

            await asyncio.sleep(wait_time)

            # 确保播放停止
            sd.stop()
            self.is_playing = False
            # 分接：播完同步收 session
            self._sink_stop()

            self.logger.debug("音频播放完成")

        except Exception as e:
            self.logger.exception(f"音频播放失败: {e}")
            self.is_playing = False
            self._sink_stop()
            raise

    def start_stream(self, utterance_id: str = "") -> None:
        """启动流式播放,创建 OutputStream 准备接收音频块。"""
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                device=self.output_device_index,
            )
            self._stream.start()
            self.is_playing = True
            # 分接：开播同步开 session（流式播法逐块 feed）
            self._sink_start(utterance_id)
            self.logger.debug("流式播放已启动")
        except Exception as e:
            self.logger.error(f"启动流式播放失败: {e}")
            self._stream = None
            self._sink_stop()

    def write_chunk(self, chunk: np.ndarray) -> None:
        """向流写入一个音频块,立即输出到扬声器。

        Args:
            chunk: 音频数据块 (1D 或 2D ndarray)
        """
        # 分接：逐块复制递入（同步、非阻塞，fail-soft）
        self._sink_feed(chunk, self.sample_rate)
        if not DEPENDENCIES_OK:
            return
        if self._stream is None:
            return
        try:
            self._stream.write(chunk)
        except Exception as e:
            self.logger.error(f"写入音频流失败: {e}")

    def stop_stream(self) -> None:
        """停止并关闭流式播放。"""
        if not DEPENDENCIES_OK:
            return
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            self.logger.warning(f"停止音频流失败: {e}")
        finally:
            self._stream = None
            self.is_playing = False
            # 分接：播完同步收 session
            self._sink_stop()
            self.logger.debug("流式播放已停止")

    def stop_audio(self) -> None:
        """停止音频播放"""
        if not DEPENDENCIES_OK:
            return

        try:
            sd.stop()
            self.is_playing = False
            self.logger.debug("音频播放已停止")
        except Exception as e:
            self.logger.warning(f"停止音频播放失败: {e}")

    def get_device_info(self, device_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        获取设备信息

        Args:
            device_index: 设备索引（默认使用当前设备）

        Returns:
            设备信息字典
        """
        if not DEPENDENCIES_OK:
            return None

        try:
            if device_index is None:
                device_index = self.output_device_index

            if device_index is None:
                # 使用默认设备
                default_device_indices = sd.default.device
                device_index = default_device_indices[1] if default_device_indices[1] != -1 else None

            if device_index is None:
                return None

            device = sd.query_devices(device_index)
            return {
                "index": device_index,
                "name": getattr(device, "name", "Unknown"),
                "max_output_channels": getattr(device, "max_output_channels", 0),
                "max_input_channels": getattr(device, "max_input_channels", 0),
                "default_samplerate": getattr(device, "default_samplerate", 0),
            }
        except Exception as e:
            self.logger.error(f"获取设备信息失败: {e}")
            return None
