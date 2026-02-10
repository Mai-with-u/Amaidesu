"""Audio utility functions for streaming."""

from typing import Tuple

import numpy as np


def to_int16_bytes(audio: np.ndarray) -> bytes:
    """将 float32 或 int16 numpy 数组转换为 int16 bytes

    Args:
        audio: numpy 数组（float32 或 int16）

    Returns:
        int16 bytes
    """
    if audio.dtype == np.float32:
        audio = (audio * 32767).astype(np.int16)
    elif audio.dtype != np.int16:
        audio = audio.astype(np.int16)
    return audio.tobytes()


def resample_audio(audio_bytes: bytes, source_rate: int, target_rate: int) -> bytes:
    """
    线性插值重采样（轻量级，不依赖 librosa）

    Args:
        audio_bytes: int16 bytes 音频数据
        source_rate: 源采样率
        target_rate: 目标采样率

    Returns:
        int16 bytes 重采样后的音频数据
    """
    if source_rate == target_rate:
        return audio_bytes

    audio = np.frombuffer(audio_bytes, dtype=np.int16)
    duration = len(audio) / source_rate
    target_len = int(duration * target_rate)

    if target_len == 0:
        return b""

    # 线性插值
    indices = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(indices, np.arange(len(audio)), audio.astype(np.float64))

    return resampled.astype(np.int16).tobytes()


def convert_audio_format(
    audio_data: bytes,
    source_rate: int,
    target_rate: int,
    source_dtype: str = "int16",
) -> Tuple[bytes, int]:
    """
    转换音频格式（采样率 + 数据类型）

    Args:
        audio_data: 音频数据
        source_rate: 源采样率
        target_rate: 目标采样率
        source_dtype: 源数据类型描述（"float32" 或 "int16"）

    Returns:
        (converted_bytes, target_rate)
    """
    if source_dtype == "float32":
        # float32 -> int16
        audio = np.frombuffer(audio_data, dtype=np.float32)
        audio = (audio * 32767).astype(np.int16)
        audio_bytes = audio.tobytes()
    else:
        audio_bytes = audio_data

    # 重采样
    if source_rate != target_rate:
        audio_bytes = resample_audio(audio_bytes, source_rate, target_rate)

    return audio_bytes, target_rate
