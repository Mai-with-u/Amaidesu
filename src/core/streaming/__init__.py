"""
Core streaming components for audio data flow

This module provides the AudioStreamChannel system for efficient audio data
distribution from TTS Providers to multiple subscribers (VTS lip sync, RemoteStream, etc.).
"""

from .audio_stream_channel import AudioStreamChannel
from .audio_chunk import AudioChunk, AudioMetadata
from .backpressure import BackpressureStrategy, SubscriberConfig, PublishResult

__all__ = [
    "AudioStreamChannel",
    "AudioChunk",
    "AudioMetadata",
    "BackpressureStrategy",
    "SubscriberConfig",
    "PublishResult",
]
