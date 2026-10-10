"""Voicebox 引擎（本地语音合成服务）。"""

from .provider import VoiceboxProvider, create_voicebox_provider

__all__ = ["VoiceboxProvider", "create_voicebox_provider"]
