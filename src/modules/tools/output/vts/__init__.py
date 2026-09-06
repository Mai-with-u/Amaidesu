"""VTS 工具模块

VTS/VRChat 虚拟形象工具集:
- ``VTSProvider``: ToolProvider 实现，把 VTS 全家桶能力（口型同步、表情、
  热键、idle 动画、贴纸）封装为一个 Provider，通过 ``ToolRegistry`` 注册
  多个工具（``set_expression``、``trigger_hotkey``、``set_parameter_value``、
  ``get_parameter_value``、``load_item``、``load_sticker``、``set_idle_enabled``、
  ``smile``、``close_eyes``、``open_eyes`` 等）。
- ``VRChatProvider``: VRChat OSC 工具集（表情参数 + 手势）。
- 引擎子件 ``LipSyncProcessor`` / ``ExpressionController`` / ``HotkeyMatcher`` /
  ``IdleMotionController`` 经 callback 解耦，可独立复用。
"""

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import AxisWander, IdleMotionController
from .lip_sync_processor import LipSyncProcessor
from .vrchat_provider import VRChatProvider, create_vrchat_provider, register_vrchat_tools
from .vts_provider import VTSProvider, create_vts_provider, register_vts_tools

__all__ = [
    "VTSProvider",
    "VRChatProvider",
    "create_vts_provider",
    "create_vrchat_provider",
    "register_vts_tools",
    "register_vrchat_tools",
    "LipSyncProcessor",
    "ExpressionController",
    "HotkeyMatcher",
    "IdleMotionController",
    "AxisWander",
]
