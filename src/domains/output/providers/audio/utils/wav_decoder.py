"""
WAV 解码工具模块

提取自:
- src/domains/output/providers/gptsovits/gptsovits_provider.py:273-331
- src/domains/output/providers/omni_tts/omni_tts_provider.py:302-320
"""

import base64
from typing import Optional

import numpy as np


def extract_pcm_from_wav(wav_data: bytes) -> bytes:
    """
    从 WAV 数据中提取 PCM 数据

    简化版本，查找 "data" chunk

    Args:
        wav_data: WAV 格式的字节数据

    Returns:
        PCM 数据字节
    """
    try:
        # WAV header is at least 44 bytes
        if len(wav_data) < 44:
            return wav_data

        # Find "data" chunk
        data_pos = wav_data.find(b"data")
        if data_pos == -1:
            return wav_data[44:]  # Skip standard header

        # Skip "data" marker and size (4 + 4 = 8 bytes)
        pcm_start = data_pos + 8
        return wav_data[pcm_start:]

    except Exception:
        # 发生错误时返回原始数据
        return wav_data


async def decode_wav_chunk(wav_chunk: bytes, dtype=np.int16) -> Optional[np.ndarray]:
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

        # 转换为 numpy 数组
        audio_array = np.frombuffer(pcm_data, dtype=dtype)
        return audio_array

     except Exception:
        self.logger.error(f"提取 PCM 数据失败: {e}", exc_info=True)
        return None
