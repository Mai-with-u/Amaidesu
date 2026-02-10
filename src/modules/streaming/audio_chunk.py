"""Audio data chunk and metadata classes for streaming."""

import time

from pydantic import BaseModel, Field


class AudioMetadata(BaseModel):
    """音频流元数据"""

    text: str = Field(description="TTS 文本内容")
    sample_rate: int = Field(description="采样率（Hz）")
    channels: int = Field(default=1, description="声道数")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    class Config:
        arbitrary_types_allowed = True


class AudioChunk(BaseModel):
    """音频数据块"""

    data: bytes = Field(description="音频数据（int16 bytes）")
    sample_rate: int = Field(description="采样率（Hz）")
    channels: int = Field(default=1, description="声道数")
    sequence: int = Field(description="序列号（用于排序）")
    timestamp: float = Field(default_factory=time.time, description="时间戳")

    def size(self) -> int:
        """返回数据大小（字节）"""
        return len(self.data)
