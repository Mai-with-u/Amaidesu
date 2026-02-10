"""STT Input Provider - 语音转文字输入 Provider"""

from src.modules.registry import ProviderRegistry

from .stt_input_provider import STTInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("stt", STTInputProvider, source="builtin:stt")

__all__ = ["STTInputProvider"]
