"""TTS 工具模块（Wave 4 迁移）

将所有 TTS Handler（Edge TTS / GPT-SoVITS / Omni TTS / Voicebox）迁移为
``@tool`` 风格的工具，引擎内 TTS 客户端初始化 / 合成 / 播放逻辑 verbatim
复用。

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- ``EdgeTTS`` / ``GPTSoVITS`` / ``Voicebox``: ConfigSchema / init / setup /
  ``_synthesize`` verbatim；仅构造器注入（``AudioStreamChannel`` 改为可选），
  工具通过 ``ToolRegistry.invoke()`` 入口调用 ``handle_speech()``。
- ``OmniTTS``: 仅迁移不改造（不补文本清洗，不修复 KeyError 已知问题）。
"""

from .device_finder import find_device_index
from .edge_tts_tool import EdgeTTSProvider, create_edge_tts_provider, register_edge_tts_tools
from .gptsovits_tool import GPTSoVITSProvider, create_gptsovits_provider, register_gptsovits_tools
from .omni_tts_tool import OmniTTSProvider, create_omni_tts_provider, register_omni_tts_tools
from .voicebox_tool import VoiceboxProvider, create_voicebox_provider, register_voicebox_tools
from .wav_decoder import decode_wav_chunk, extract_pcm_from_wav

__all__ = [
    "EdgeTTSProvider",
    "GPTSoVITSProvider",
    "OmniTTSProvider",
    "VoiceboxProvider",
    "create_edge_tts_provider",
    "create_gptsovits_provider",
    "create_omni_tts_provider",
    "create_voicebox_provider",
    "register_edge_tts_tools",
    "register_gptsovits_tools",
    "register_omni_tts_tools",
    "register_voicebox_tools",
    "decode_wav_chunk",
    "extract_pcm_from_wav",
    "find_device_index",
]
