"""VTS 工具模块

VTubeStudio 虚拟形象工具集:
- ``VTSProvider``: ToolProvider 实现，管连接、授权、断线重连、换模跟随与热键，通过
  ``ToolRegistry`` 注册契约工具（``vts_set_expression``、``vts_list_preset_actions``、
  ``vts_trigger_preset_action``、``vts_set_idle_enabled``）。
- ``VtsTrackingDriver``: 面捕驱动，把面捕合成器的每一帧换算成 VTS 捕捉输入整组注入。
- ``VtsClient``: 插件接口客户端，请求按编号对回复，逐帧注入发完就走。

VRChat OSC 桥接是独立虚拟形象平台，位于 ``src/modules/avatar/platform/vrchat/``。
"""

from .hotkey_matcher import HotkeyMatcher
from .tracking_driver import VtsTrackingDriver
from .vts_client import VtsApiError, VtsClient
from .vts_provider import VTSProvider, create_vts_provider, register_vts_tools

__all__ = [
    "HotkeyMatcher",
    "VTSProvider",
    "VtsApiError",
    "VtsClient",
    "VtsTrackingDriver",
    "create_vts_provider",
    "register_vts_tools",
]
