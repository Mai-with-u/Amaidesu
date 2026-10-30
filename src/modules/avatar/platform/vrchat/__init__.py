"""VRChat 适配器（OSC）

``VRChatProvider`` 把面捕合成器的一帧写成 VRChat 的 OSC 输入：眼睛走官方眼动输入，脸走
VRCFaceTracking 的通行参数名；动作表登记为动作片段，头顶聊天框登记为字幕后端。不向 LLM
暴露工具。
"""

from .vrchat_provider import VRChatProvider, create_vrchat_provider, register_vrchat_tools

__all__ = [
    "VRChatProvider",
    "create_vrchat_provider",
    "register_vrchat_tools",
]
