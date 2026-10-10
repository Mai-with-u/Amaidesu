"""Edge TTS 引擎（微软在线语音合成）。"""

from .provider import EdgeTTSProvider, create_edge_tts_provider

__all__ = ["EdgeTTSProvider", "create_edge_tts_provider"]
