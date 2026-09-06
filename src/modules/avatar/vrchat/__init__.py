"""VRChat 工具模块

VRChat OSC 桥接（独立形象后端，与 VTS 无关）:
- ``VRChatProvider``: ToolProvider 实现，把 VRChat OSC 能力（表情参数 + 手势）
  封装为一个 Provider，通过 ``ToolRegistry`` 注册工具（``vrchat_set_expression``、
  ``vrchat_trigger_gesture``、``vrchat_get_stats``）。
"""

from .vrchat_provider import VRChatProvider, create_vrchat_provider, register_vrchat_tools

__all__ = [
    "VRChatProvider",
    "create_vrchat_provider",
    "register_vrchat_tools",
]
