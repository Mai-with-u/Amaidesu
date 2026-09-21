"""VTS 工具模块

VTubeStudio 虚拟形象工具集:
- ``VTSProvider``: ToolProvider 实现，把 VTS 能力（情绪表情、预设动作、
  idle 动画、口型同步）封装为一个 Provider，通过 ``ToolRegistry`` 注册
  契约工具（``vts_set_expression``、``vts_list_preset_actions``、
  ``vts_trigger_preset_action``、``vts_set_idle_enabled``）。
- 引擎子件 ``ExpressionController`` / ``HotkeyMatcher`` /
  ``IdleMotionController`` 经 callback 解耦，可独立复用。

VRChat OSC 桥接是独立皮套平台，位于 ``src/modules/avatar/platform/vrchat/``。
"""

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import AxisWander, IdleMotionController
from .lip_sync_renderer import VtsLipSyncRenderer
from .vts_provider import VTSProvider, create_vts_provider, register_vts_tools

__all__ = [
    "VTSProvider",
    "create_vts_provider",
    "register_vts_tools",
    "VtsLipSyncRenderer",
    "ExpressionController",
    "HotkeyMatcher",
    "IdleMotionController",
    "AxisWander",
]
