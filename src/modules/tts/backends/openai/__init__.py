"""OpenAI 语音合成引擎（``openai_tts``）

对接 OpenAI ``POST /v1/audio/speech`` 端点：请求体描述模型、音色与风格
提示，响应体是裸 PCM 字节流，逐块写声卡播放。
"""

from .provider import OpenAITTSProvider, create_openai_tts_provider

__all__ = ["OpenAITTSProvider", "create_openai_tts_provider"]
