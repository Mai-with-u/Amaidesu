"""
音频输出Provider模块

包含:
- EdgeTTSProvider: Edge TTS文本转语音
- GPTSoVITSOutputProvider: GPT-SoVITS文本转语音
- OmniTTSProvider: Omni TTS文本转语音
"""

from src.core.provider_registry import ProviderRegistry

from .edge_tts_provider import EdgeTTSProvider
from .gptsovits_provider import GPTSoVITSOutputProvider
from .omni_tts_provider import OmniTTSProvider

# 向后兼容别名
TTSProvider = EdgeTTSProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("edge_tts", EdgeTTSProvider, source="builtin:edge_tts")
ProviderRegistry.register_output("tts", EdgeTTSProvider, source="builtin:tts")  # 向后兼容
ProviderRegistry.register_output("gptsovits", GPTSoVITSOutputProvider, source="builtin:gptsovits")
ProviderRegistry.register_output("omni_tts", OmniTTSProvider, source="builtin:omni_tts")

__all__ = [
    "EdgeTTSProvider",
    "TTSProvider",  # 向后兼容别名
    "GPTSoVITSOutputProvider",
    "OmniTTSProvider",
]
