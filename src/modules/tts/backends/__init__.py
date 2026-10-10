"""TTS 引擎后端集合。

每个子包一个引擎（provider.py 为引擎本体，引擎私有的辅助实现与它同包），
实现 ``src.modules.tts.protocol`` 的 ``TTSProvider`` 结构契约；装配入口
``build_tts_infrastructure`` 按 ``[tts].provider`` 配置构造选中引擎。

当前内置引擎：

- ``edge``：Edge TTS（微软在线语音）
- ``gptsovits``：GPT-SoVITS 本地服务（api_v2 对接，声音克隆）
- ``voicebox``：Voicebox 本地服务
- ``omni``：Omni TTS
"""

from .edge.provider import EdgeTTSProvider, create_edge_tts_provider
from .gptsovits.provider import GPTSoVITSProvider, create_gptsovits_provider
from .omni.provider import OmniTTSProvider, create_omni_tts_provider
from .voicebox.provider import VoiceboxProvider, create_voicebox_provider

__all__ = [
    "EdgeTTSProvider",
    "GPTSoVITSProvider",
    "OmniTTSProvider",
    "VoiceboxProvider",
    "create_edge_tts_provider",
    "create_gptsovits_provider",
    "create_omni_tts_provider",
    "create_voicebox_provider",
]
