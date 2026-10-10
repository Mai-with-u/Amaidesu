"""
WAV 解码工具模块
"""

import base64
from typing import Any, Optional

import numpy as np

from src.modules.logging import get_logger

logger = get_logger("WavDecoder")


def extract_pcm_from_wav(wav_data: bytes) -> bytes:
    """
    从 WAV 数据中提取 PCM 数据

    以 RIFF 魔数判定是否携带 WAV 头：带头的块（完整 WAV 或 api_v2 流式
    首块）解析 data 段；裸 PCM 块（api_v2 流式后续块）原样返回。裸 PCM
    内容里可能偶然出现 "data" 四字节序列，不得用 find 探测无头块。

    Args:
        wav_data: WAV 格式或 raw PCM 字节数据

    Returns:
        PCM 数据字节
    """
    try:
        if wav_data[:4] == b"RIFF":
            # Skip "data" marker and size (4 + 4 = 8 bytes)；头至少 12 字节后才可能出现 data 标识
            data_pos = wav_data.find(b"data", 12)
            if data_pos != -1:
                return wav_data[data_pos + 8 :]
        # 无 RIFF 头（短碎块 / raw PCM 流式块）：直接全部返回
        return wav_data

    except Exception as e:
        logger.warning(f"WAV header 解析失败，返回原始数据: {e}")
        return wav_data


async def decode_wav_chunk(wav_chunk: bytes, dtype: Any = np.int16) -> Optional[np.ndarray]:
    """
    解码 WAV 数据块

    处理 base64 编码，解析 WAV 头部，提取 PCM 数据

    Args:
        wav_chunk: WAV 数据块（bytes 或 base64 编码的字符串）
        dtype: numpy 数据类型

    Returns:
        numpy 数组或 None
    """
    try:
        # 处理 base64 编码
        if isinstance(wav_chunk, str):
            wav_data = base64.b64decode(wav_chunk)
        else:
            wav_data = wav_chunk

        # 提取 PCM 数据
        pcm_data = extract_pcm_from_wav(wav_data)

        # 网络分块边界不保证按样本对齐：截掉不足一个样本的尾字节
        itemsize = np.dtype(dtype).itemsize
        remainder = len(pcm_data) % itemsize
        if remainder:
            pcm_data = pcm_data[:-remainder]

        # 转换为 numpy 数组
        audio_array = np.frombuffer(pcm_data, dtype=dtype)
        return audio_array

    except Exception as e:
        logger.warning(f"WAV chunk 解码失败，返回 None: {e}")
        return None
