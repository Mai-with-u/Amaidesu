"""
音频设备查找工具模块

封装 AudioDeviceManager 服务
"""

from typing import Optional

import numpy as np

from src.modules.tts import AudioDeviceManager


def find_device_index(
    device_name: Optional[str], logger, sample_rate: int = 48000, channels: int = 1, dtype=None
) -> Optional[int]:
    """
    查找音频设备索引

    合并 TTSProvider._find_device_index 和 OmniTTSProvider._find_device_index 的逻辑

    Args:
        device_name: 设备名称（可选）
        logger: 日志记录器
        sample_rate: 采样率
        channels: 声道数
        dtype: 数据类型

    Returns:
        设备索引，如果未找到则返回 None
    """
    try:
        # 创建 AudioDeviceManager 实例
        audio_manager = AudioDeviceManager(sample_rate=sample_rate, channels=channels, dtype=dtype or np.int16)

        # 查找设备
        device_index = audio_manager.find_device_index(device_name)

        if device_index is not None:
            logger.info(f"找到音频设备 '{device_name}'，索引: {device_index}")
        elif device_name:
            logger.warning(f"未找到音频设备 '{device_name}'")
        else:
            logger.debug("使用默认音频设备")

        return device_index

    except Exception as e:
        logger.error(f"查找音频设备失败: {e}")
        return None
