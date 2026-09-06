"""VTS 工具模块

VTubeStudio 虚拟形象工具集:
- ``VTSProvider``: ToolProvider 实现，把 VTS 全家桶能力（口型同步、表情、
  热键、idle 动画、贴纸）封装为一个 Provider，通过 ``ToolRegistry`` 注册
  多个工具（``vts_set_expression``、``vts_trigger_hotkey``、
  ``vts_set_parameter_value``、``vts_get_parameter_value``、``vts_load_item``、
  ``vts_load_sticker``、``vts_set_idle_enabled``、``vts_smile``、
  ``vts_close_eyes``、``vts_open_eyes`` 等）。
- 引擎子件 ``LipSyncProcessor`` / ``ExpressionController`` / ``HotkeyMatcher`` /
  ``IdleMotionController`` 经 callback 解耦，可独立复用。

VRChat OSC 桥接是独立形象后端，位于 ``src/modules/avatar/vrchat/``。
"""

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import AxisWander, IdleMotionController
from .lip_sync_processor import LipSyncProcessor
from .vts_provider import VTSProvider, create_vts_provider, register_vts_tools

__all__ = [
    "VTSProvider",
    "create_vts_provider",
    "register_vts_tools",
    "LipSyncProcessor",
    "ExpressionController",
    "HotkeyMatcher",
    "IdleMotionController",
    "AxisWander",
]
