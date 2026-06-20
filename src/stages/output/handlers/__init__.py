"""
Rendering Handlers - 渲染输出Handler实现

包含各种 OutputHandler 的具体实现：
- SubtitleHandler: 字幕输出Handler
- EdgeTTSHandler: Edge TTS语音输出Handler
- GPTSoVITSHandler: GPT-SoVITS TTS Handler
- OmniTTSHandler: Omni TTS Handler
- VTSHandler: VTS虚拟形象Handler（位于avatar/vts/）
- WarudoHandler: Warudo虚拟形象Handler（位于avatar/warudo/）
- StickerHandler: 贴纸输出Handler
- ObsControlHandler: OBS控制Handler
- RemoteStreamHandler: 远程流输出Handler
- DebugConsoleHandler: 调试控制台输出Handler

注意：导入这些模块会触发自动注册到 _HANDLERS
"""

# 导入所有 Handler 子模块以触发 @handler 装饰器注册
from . import (
    avatar,  # noqa: F401 (包含所有Avatar Handler，包括VTSHandler, WarudoHandler, VRChatHandler)
    debug_console,  # noqa: F401
    edge_tts,  # noqa: F401
    gptsovits,  # noqa: F401
    obs_control,  # noqa: F401
    omni_tts,  # noqa: F401
    remote_stream,  # noqa: F401
    sticker,  # noqa: F401
    subtitle,  # noqa: F401
)
from .avatar.vts import VTSHandler
from .avatar.warudo import WarudoHandler
from .avatar.vrchat import VRChatHandler
from .debug_console import DebugConsoleHandler
from .edge_tts import EdgeTTSHandler
from .gptsovits import GPTSoVITSHandler
from .obs_control import ObsControlHandler
from .omni_tts import OmniTTSHandler
from .remote_stream import RemoteStreamHandler
from .sticker import StickerHandler
from .subtitle import SubtitleHandler

# 同时导出类以便直接使用
from .mock import MockOutputHandler

__all__ = [
    "SubtitleHandler",
    "EdgeTTSHandler",
    "GPTSoVITSHandler",
    "OmniTTSHandler",
    "VTSHandler",
    "WarudoHandler",
    "VRChatHandler",
    "StickerHandler",
    "ObsControlHandler",
    "RemoteStreamHandler",
    "DebugConsoleHandler",
    "MockOutputHandler",
]
