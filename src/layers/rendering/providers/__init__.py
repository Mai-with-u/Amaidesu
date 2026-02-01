"""
Rendering Providers - 渲染输出Provider实现

包含各种 OutputProvider 的具体实现：
- SubtitleOutputProvider: 字幕输出Provider
- TTSProvider: TTS语音输出Provider
- VTSProvider: VTS虚拟形象Provider
- StickerOutputProvider: 贴纸输出Provider
- WarudoOutputProvider: Warudo虚拟形象Provider
- ObsControlOutputProvider: OBS控制Provider
- GPTSoVITSOutputProvider: GPT-SoVITS TTS Provider
- OmniTTSProvider: Omni TTS Provider
- AvatarOutputProvider: 虚拟形象输出Provider
"""

from .subtitle import SubtitleOutputProvider
from .tts import TTSProvider
from .vts import VTSProvider
from .sticker import StickerOutputProvider
from .warudo import WarudoOutputProvider
from .obs_control import ObsControlOutputProvider
from .gptsovits import GPTSoVITSOutputProvider
from .omni_tts import OmniTTSProvider
from .avatar import AvatarOutputProvider

__all__ = [
    "SubtitleOutputProvider",
    "TTSProvider",
    "VTSProvider",
    "StickerOutputProvider",
    "WarudoOutputProvider",
    "ObsControlOutputProvider",
    "GPTSoVITSOutputProvider",
    "OmniTTSProvider",
    "AvatarOutputProvider",
]
