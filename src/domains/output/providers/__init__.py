"""
Rendering Providers - 渲染输出Provider实现

包含各种 OutputProvider 的具体实现：
- SubtitleOutputProvider: 字幕输出Provider
- EdgeTTSProvider: Edge TTS语音输出Provider
- GPTSoVITSOutputProvider: GPT-SoVITS TTS Provider
- OmniTTSProvider: Omni TTS Provider
- VTSProvider: VTS虚拟形象Provider
- StickerOutputProvider: 贴纸输出Provider
- WarudoOutputProvider: Warudo虚拟形象Provider
- ObsControlOutputProvider: OBS控制Provider
- AvatarOutputProvider: 虚拟形象输出Provider
- RemoteStreamOutputProvider: 远程流输出Provider
- MockOutputProvider: 模拟输出Provider（用于测试）

注意：导入这些模块会触发自动注册到 ProviderRegistry
"""

# 导入所有 Provider 子模块以触发自动注册（导入__init__.py会触发注册）
from . import subtitle  # noqa: F401
from . import audio  # noqa: F401 (包含 EdgeTTSProvider, GPTSoVITSOutputProvider, OmniTTSProvider)
from . import vts  # noqa: F401
from . import sticker  # noqa: F401
from . import warudo  # noqa: F401
from . import obs_control  # noqa: F401
from . import avatar  # noqa: F401
from . import remote_stream  # noqa: F401
from . import mock  # noqa: F401

# 同时导出类以便直接使用
from .subtitle import SubtitleOutputProvider
from .audio import EdgeTTSProvider, GPTSoVITSOutputProvider, OmniTTSProvider
from .vts import VTSProvider
from .sticker import StickerOutputProvider
from .warudo import WarudoOutputProvider
from .obs_control import ObsControlOutputProvider
from .avatar import AvatarOutputProvider
from .remote_stream import RemoteStreamOutputProvider
from .mock import MockOutputProvider

__all__ = [
    "SubtitleOutputProvider",
    "EdgeTTSProvider",
    "GPTSoVITSOutputProvider",
    "OmniTTSProvider",
    "VTSProvider",
    "StickerOutputProvider",
    "WarudoOutputProvider",
    "ObsControlOutputProvider",
    "AvatarOutputProvider",
    "RemoteStreamOutputProvider",
    "MockOutputProvider",
]
