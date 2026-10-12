"""wav_decoder 测试

覆盖 api_v2 流式 wav 的块形态：首块 = 44 字节 WAV 头（长度字段为 0），
后续块 = 裸 PCM；以及分块边界不对齐时的样本对齐防御。
"""

from __future__ import annotations

import struct

import numpy as np

from src.modules.tts.wav_decoder import decode_wav_chunk, extract_pcm_from_wav


def _build_wav_header(pcm_size_placeholder: int = 0) -> bytes:
    """构造 44 字节标准 PCM WAV 头（api_v2 流式首块的形态，长度字段可填 0）"""
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + pcm_size_placeholder),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack("<HHIIHH", 1, 1, 32000, 64000, 2, 16),
            b"data",
            struct.pack("<I", pcm_size_placeholder),
        ]
    )


class TestExtractPcmFromWav:
    def test_full_wav_header_stripped(self):
        """完整 WAV（RIFF 头 + data 段）：返回 data 段后的 PCM"""
        pcm = b"\x01\x02\x03\x04"
        wav = _build_wav_header(len(pcm)) + pcm
        assert extract_pcm_from_wav(wav) == pcm

    def test_streaming_first_chunk_header_only(self):
        """api_v2 流式首块（44B 头、长度字段 0、无音频数据）：返回空"""
        assert extract_pcm_from_wav(_build_wav_header(0)) == b""

    def test_raw_pcm_block_with_data_magic_inside(self):
        """裸 PCM 块内容恰好含 b"data" 魔数：必须原样返回，不得被 find 误判截断"""
        payload = b"abcd" + b"data" + b"\xff\xee"
        assert extract_pcm_from_wav(payload) == payload

    def test_short_fragment_returned_as_is(self):
        """短碎块（非 RIFF）：原样返回"""
        assert extract_pcm_from_wav(b"\x01\x02") == b"\x01\x02"


class TestDecodeWavChunk:
    async def test_streaming_chunk_sequence_decodes(self):
        """首块（头+部分PCM）+ 后续裸块：逐块解码帧数等于总样本数"""
        samples = np.arange(1000, dtype=np.int16).tobytes()
        first_chunk = _build_wav_header(0) + samples[:400]
        rest = samples[400:]

        first = await decode_wav_chunk(first_chunk, dtype=np.int16)
        assert first is not None
        assert first.tobytes() == samples[:400]

        second = await decode_wav_chunk(rest, dtype=np.int16)
        assert second is not None
        assert second.tobytes() == samples[400:]

    async def test_odd_sized_tail_chunk_aligned(self):
        """奇数字节块（分块边界切在样本中间）：截掉尾字节正常解码"""
        payload = b"\x01\x02\x03\x04\x05"  # 2.5 个样本
        decoded = await decode_wav_chunk(payload, dtype=np.int16)
        assert decoded is not None
        assert len(decoded) == 2
        assert decoded.tobytes() == b"\x01\x02\x03\x04"
