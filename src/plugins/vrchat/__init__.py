"""
VRChat Plugin

通过 OSC 协议连接到 VRChat，控制虚拟形象参数。

依赖：
    pip install python-osc

使用示例：
    # 自动文本分析并设置表情
    await core.avatar.set_expression_from_text("你好！", adapter_name="vrc")

    # 手动设置表情
    await core.avatar.set_semantic_action("happy_expression", 0.8, adapter_name="vrc")

    # 直接控制参数
    await core.avatar.set_semantic_action("close_eyes", 1.0, adapter_name="vrc")
"""

from .plugin import VRChatPlugin

__all__ = ["VRChatPlugin"]
