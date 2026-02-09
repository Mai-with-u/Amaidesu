"""STT Input Provider - 语音转文字输入 Provider"""

from .stt_input_provider import STTInputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("stt", STTInputProvider, source="builtin:stt")

__all__ = ["STTInputProvider"]
